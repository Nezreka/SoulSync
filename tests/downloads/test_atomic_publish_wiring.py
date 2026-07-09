"""Wiring for atomic album publishing (#999): the pipeline stage-redirect gate
and the lifecycle publish hook. The safety-critical guarantee is that with the
flag OFF (default) the redirect is a pure pass-through and never touches batch
state — i.e. normal downloads are byte-for-byte unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

import core.imports.pipeline as pl
import core.downloads.lifecycle as lc


class _Cfg:
    def __init__(self, vals):
        self.vals = vals

    def get(self, key, default=None):
        return self.vals.get(key, default)


def _wire(monkeypatch, tmp_path, *, flag, batch):
    transfer = str(tmp_path / "music")
    monkeypatch.setattr(pl, "config_manager", _Cfg({
        "album_downloads.atomic_publish": flag,
        "soulseek.transfer_path": transfer,
    }))
    monkeypatch.setattr(pl, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(pl, "download_batches", {"B": batch})
    return transfer


# --- the safety guarantee: flag OFF is a no-op pass-through -----------------

def test_flag_off_returns_unchanged_and_never_touches_batch(monkeypatch, tmp_path):
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=False, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    assert pl._maybe_stage_album_track({"batch_id": "B"}, final) == final
    assert batch == {"is_album_download": True}  # not decided, not mutated at all


def test_flag_on_but_not_album_batch_unchanged(monkeypatch, tmp_path):
    batch = {"is_album_download": False}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    assert pl._maybe_stage_album_track({"batch_id": "B"}, final) == final


def test_flag_on_no_batch_id_unchanged(monkeypatch, tmp_path):
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch={"is_album_download": True})
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    assert pl._maybe_stage_album_track({}, final) == final


def test_batch_id_read_from_wrapper_stash_key(monkeypatch, tmp_path):
    # Real batched downloads go through the verification wrapper, which pops
    # batch_id and stashes it under _atomic_publish_batch_id. The redirect must
    # still find it (this is the fix for "album published directly despite ON").
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    staged = pl._maybe_stage_album_track({"_atomic_publish_batch_id": "B"}, final)
    assert staged != final and batch["_atomic_active"] is True


# --- the gate: flag ON + fresh whole-album batch redirects to staging -------

def test_fresh_album_redirects_to_staging_and_marks_batch(monkeypatch, tmp_path):
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")  # dir doesn't exist → fresh

    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)

    assert staged != final
    assert batch["_atomic_active"] is True
    assert batch["_atomic_transfer_dir"] == transfer
    # The staged path maps back to the same final path.
    from core.downloads.atomic_album_publish import to_final_path
    assert os.path.normpath(to_final_path(staged, batch["_atomic_staging_root"], transfer)) \
        == os.path.normpath(final)


def test_existing_album_folder_is_not_staged(monkeypatch, tmp_path):
    # A completeness-fill: the album folder already holds audio → NOT fresh →
    # keep today's per-track publish (no staging).
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    album_dir = Path(transfer) / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    (album_dir / "01 - owned.flac").write_bytes(b"x")
    final = str(album_dir / "02 - new.flac")

    assert pl._maybe_stage_album_track({"batch_id": "B"}, final) == final
    assert batch["_atomic_active"] is False


def test_decision_is_cached_across_tracks(monkeypatch, tmp_path):
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    f1 = os.path.join(transfer, "Artist", "Album", "01.flac")
    s1 = pl._maybe_stage_album_track({"batch_id": "B"}, f1)
    root_after_first = batch["_atomic_staging_root"]
    # Second track reuses the cached decision + same staging root.
    f2 = os.path.join(transfer, "Artist", "Album", "02.flac")
    s2 = pl._maybe_stage_album_track({"batch_id": "B"}, f2)
    assert s1 != f1 and s2 != f2
    assert batch["_atomic_staging_root"] == root_after_first


# --- lifecycle publish hook: no-op unless the batch was staged --------------

def test_publish_hook_noop_when_not_active():
    # A normal (non-staged) batch: the hook must do nothing and never raise.
    lc._publish_atomic_album("B", {"is_album_download": True})  # no _atomic_active
    lc._publish_atomic_album("B", {})  # empty batch


def test_publish_hook_noop_when_staging_missing(tmp_path):
    # Marked active but the staging dir doesn't exist (nothing was staged) → no-op.
    lc._publish_atomic_album("B", {
        "_atomic_active": True,
        "_atomic_staging_root": str(tmp_path / "gone"),
        "_atomic_transfer_dir": str(tmp_path / "music"),
    })


# --- end-to-end: pipeline stage-redirect → batch-complete publish -----------

def test_end_to_end_stage_then_publish(monkeypatch, tmp_path):
    from pathlib import Path

    # 1) The pipeline redirects a fresh-album track to staging.
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01 - Song.flac")
    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)
    assert staged != final and batch["_atomic_active"] is True

    # Simulate post-processing having written the file (and a sidecar) into staging,
    # and the DB/consistency roster recording the staged path (as the pipeline does).
    Path(staged).parent.mkdir(parents=True, exist_ok=True)
    Path(staged).write_bytes(b"AUDIO")
    Path(os.path.join(os.path.dirname(staged), "folder.jpg")).write_bytes(b"ART")
    batch["_consistency_files"] = [{"path": staged, "track_number": 1}]

    # 2) At batch completion, publish moves staging → library, repoints DB, remaps roster.
    db_updates = []

    class _FakeConn:
        def cursor(self): return self
        def execute(self, q, params): db_updates.append(params)
        def commit(self): pass
        def close(self): pass

    class _FakeDB:
        def _get_connection(self): return _FakeConn()

    monkeypatch.setattr("database.music_database.MusicDatabase", _FakeDB)

    lc._publish_atomic_album("B", batch)

    # File (and sidecar) now live in the library; staging is emptied/pruned.
    assert os.path.isfile(final)
    assert os.path.isfile(os.path.join(transfer, "Artist", "Album", "folder.jpg"))
    assert not os.path.exists(staged)
    assert not os.path.exists(batch["_atomic_staging_root"])
    # DB repointed staged → final for the audio track.
    assert (final, staged) in db_updates
    # Consistency roster now points at the published file (album-consistency runs next).
    assert batch["_consistency_files"][0]["path"] == final


def test_publish_reregisters_final_folder_with_repair(monkeypatch, tmp_path):
    from pathlib import Path

    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)
    Path(staged).parent.mkdir(parents=True, exist_ok=True)
    Path(staged).write_bytes(b"A")

    class _FakeConn:
        def cursor(self): return self
        def execute(self, q, p): pass
        def commit(self): pass
        def close(self): pass

    class _FakeDB:
        def _get_connection(self): return _FakeConn()

    monkeypatch.setattr("database.music_database.MusicDatabase", _FakeDB)

    registered = []

    class _Deps:
        class repair_worker:  # noqa: N801 — stand-in
            @staticmethod
            def register_folder(bid, folder):
                registered.append((bid, folder))

    lc._publish_atomic_album("B", batch, _Deps())
    # The PUBLISHED album folder (not the emptied staging one) is registered so
    # the post-batch track-number repair scans real files.
    assert registered == [("B", os.path.join(transfer, "Artist", "Album"))]
