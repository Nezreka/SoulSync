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
