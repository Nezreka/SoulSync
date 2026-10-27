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


# --- L2-002: a failed publish must not produce a Complete batch -------------


def _staged_batch(monkeypatch, tmp_path, name="F"):
    """A batch with one staged track, ready to publish."""
    from pathlib import Path

    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01 - Song.flac")
    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)
    Path(staged).parent.mkdir(parents=True, exist_ok=True)
    Path(staged).write_bytes(b"AUDIO")
    return batch, staged, final


def test_publish_hook_reports_success(monkeypatch, tmp_path):
    batch, staged, final = _staged_batch(monkeypatch, tmp_path)

    class _FakeConn:
        rowcount = 1

        def cursor(self): return self
        def execute(self, q, params): pass
        def commit(self): pass
        def close(self): pass

    class _FakeDB:
        def _get_connection(self): return _FakeConn()

    monkeypatch.setattr("database.music_database.MusicDatabase", _FakeDB)

    assert lc._publish_atomic_album("B", batch) is True
    assert os.path.isfile(final)


def test_publish_hook_reports_failure_and_leaves_the_album_staged(monkeypatch, tmp_path):
    """The result used to be logged and discarded, so the caller marked the
    batch complete and emitted history/scan/completion events for an album that
    never reached the library."""
    batch, staged, final = _staged_batch(monkeypatch, tmp_path)

    def _no_move(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("core.imports.file_ops.safe_move_file", _no_move)

    class _FakeDB:
        def _get_connection(self):  # never reached
            raise AssertionError("no move, no repoint")

    monkeypatch.setattr("database.music_database.MusicDatabase", _FakeDB)

    assert lc._publish_atomic_album("B", batch) is False
    assert os.path.isfile(staged)
    assert not os.path.isfile(final)


def test_a_noop_publish_still_reports_success(monkeypatch, tmp_path):
    """A batch that never staged anything is not a failed publish."""
    assert lc._publish_atomic_album("B", {"is_album_download": True}) is True
    assert lc._publish_atomic_album("B", {
        "_atomic_active": True,
        "_atomic_staging_root": str(tmp_path / "gone"),
        "_atomic_transfer_dir": str(tmp_path / "music"),
    }) is True


def test_rollback_repoints_a_real_sqlite_row(monkeypatch, tmp_path):
    """The rollback repoint against a REAL database, not a fake connection.

    The unit tests drive `publish_album_batch` with a dict standing in for the
    tracks table, which proves the logic and not the wiring. This one runs the
    actual `_db_update` closure from lifecycle against real sqlite, so the SQL,
    the commit and the reverse call are all exercised: track one publishes and
    repoints for real, track two's move fails, and the row has to come back.
    """
    import database.music_database as mdb_mod
    import core.imports.file_ops as fops
    from pathlib import Path

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    monkeypatch.setattr(mdb_mod, "MusicDatabase", lambda *a, **k: db)

    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    one = pl._maybe_stage_album_track(
        {"batch_id": "B"}, os.path.join(transfer, "Artist", "Album", "01.flac"))
    two = os.path.join(os.path.dirname(one), "02.flac")
    for p in (one, two):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_bytes(b"AUDIO")

    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist')")
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Album')")
    conn.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
        "VALUES (1, 1, 1, 'Song', ?)", (one,))
    conn.commit()
    conn.close()

    real_move = fops.safe_move_file

    def _second_fails(src, dst):
        if str(src).endswith("02.flac"):
            raise OSError("disk full")
        return real_move(src, dst)

    monkeypatch.setattr(fops, "safe_move_file", _second_fails)

    assert lc._publish_atomic_album("B", batch) is False

    conn = db._get_connection()
    stored = conn.execute("SELECT file_path FROM tracks WHERE id = 1").fetchone()[0]
    conn.close()

    assert stored == one, "the library row followed the file back to staging"
    assert os.path.isfile(stored), "no row may point at a file that is not there"


def _real_db_batch(monkeypatch, tmp_path, server):
    """A staged one-track batch over a REAL sqlite db, with the active media
    server pinned — the axis the rowcount guard's meaning turns on."""
    import database.music_database as mdb_mod
    from pathlib import Path
    from core.settings import config_manager

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    monkeypatch.setattr(mdb_mod, "MusicDatabase", lambda *a, **k: db)
    monkeypatch.setattr(config_manager, "get_active_media_server", lambda: server)

    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    final = os.path.join(transfer, "Artist", "Album", "01.flac")
    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)
    Path(staged).parent.mkdir(parents=True, exist_ok=True)
    Path(staged).write_bytes(b"AUDIO")
    return batch, staged, final


def test_a_media_server_install_publishes_with_no_track_rows(monkeypatch, tmp_path):
    """The Lil-Uzi-Chimp bug (Docker + Navidrome): rows with a staged path are
    only ever written by record_soulsync_library_entry, which is gated on the
    active server being 'soulsync' — on a Plex/Navidrome/Jellyfin install there
    is legitimately NO row until the server scans the PUBLISHED files. The
    rowcount guard read that 0 as 'the library would dangle' and rolled every
    atomic album back into .soulsync_atomic_staging, forever."""
    batch, staged, final = _real_db_batch(monkeypatch, tmp_path, "navidrome")
    assert lc._publish_atomic_album("B", batch) is True
    assert os.path.isfile(final), "the album never left staging"
    assert not os.path.isfile(staged)


def test_a_soulsync_install_still_fails_on_a_dangling_row_count(monkeypatch, tmp_path):
    """...but where the rows ARE ours (active server 'soulsync'), a zero count is
    still the proof of a dangle the guard was built for, and the publish must
    keep rolling back rather than strand the library on a staging path."""
    batch, staged, final = _real_db_batch(monkeypatch, tmp_path, "soulsync")
    assert lc._publish_atomic_album("B", batch) is False
    assert os.path.isfile(staged), "the failed publish must keep the staged copy"
    assert not os.path.isfile(final)


def _ledger_row(db, path):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO track_downloads (track_id, file_path, source_service, track_title, status) "
        "VALUES ('t1', ?, 'youtube', 'Song', 'completed')", (path,))
    conn.commit()
    conn.close()


def _ledger_path(db):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT file_path FROM track_downloads").fetchone()[0]
    finally:
        conn.close()


def test_publish_repoints_the_download_ledger(monkeypatch, tmp_path):
    """The ledger records the staging path the import landed at. After the
    publish that folder is gone, and anything asking the ledger whether a
    download still exists (ownership for artists routed to the audiobook
    library) answered no, so the album was fetched again on every scan.
    On a media-server install there is no ``tracks`` row to follow the file,
    so the ledger is the only record that has to move."""
    batch, staged, final = _real_db_batch(monkeypatch, tmp_path, "navidrome")
    import database.music_database as mdb_mod
    db = mdb_mod.MusicDatabase()
    _ledger_row(db, staged)

    assert lc._publish_atomic_album("B", batch) is True

    assert _ledger_path(db) == final
    assert os.path.isfile(_ledger_path(db)), "the ledger points at a file that is not there"


def test_rollback_takes_the_download_ledger_back(monkeypatch, tmp_path):
    """The same function publishes and un-publishes a row, so a failed batch
    leaves the ledger on the staging path, with the files."""
    import database.music_database as mdb_mod
    import core.imports.file_ops as fops
    from pathlib import Path

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    monkeypatch.setattr(mdb_mod, "MusicDatabase", lambda *a, **k: db)
    batch = {"is_album_download": True}
    transfer = _wire(monkeypatch, tmp_path, flag=True, batch=batch)
    one = pl._maybe_stage_album_track(
        {"batch_id": "B"}, os.path.join(transfer, "Artist", "Album", "01.flac"))
    two = os.path.join(os.path.dirname(one), "02.flac")
    for p in (one, two):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_bytes(b"AUDIO")
    _ledger_row(db, one)

    real_move = fops.safe_move_file

    def _second_fails(src, dst):
        if str(src).endswith("02.flac"):
            raise OSError("disk full")
        return real_move(src, dst)

    monkeypatch.setattr(fops, "safe_move_file", _second_fails)

    assert lc._publish_atomic_album("B", batch) is False
    assert _ledger_path(db) == one
    assert os.path.isfile(one)


def test_a_database_without_the_ledger_still_publishes(monkeypatch, tmp_path):
    """An old database may not have the table yet; that must not strand an album
    in staging."""
    batch, staged, final = _real_db_batch(monkeypatch, tmp_path, "navidrome")
    import database.music_database as mdb_mod
    db = mdb_mod.MusicDatabase()
    conn = db._get_connection()
    conn.execute("DROP TABLE track_downloads")
    conn.commit()
    conn.close()

    assert lc._publish_atomic_album("B", batch) is True
    assert os.path.isfile(final)


def _history_row(db, path):
    conn = db._get_connection()
    conn.execute("INSERT INTO library_history (event_type, title, file_path) "
                 "VALUES ('download', 'Song', ?)", (path,))
    conn.commit()
    conn.close()


def _history_path(db):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT file_path FROM library_history").fetchone()[0]
    finally:
        conn.close()


def test_publish_repoints_library_history_too(monkeypatch, tmp_path):
    batch, staged, final = _real_db_batch(monkeypatch, tmp_path, "navidrome")
    import database.music_database as mdb_mod
    db = mdb_mod.MusicDatabase()
    _history_row(db, staged)

    assert lc._publish_atomic_album("B", batch) is True

    assert _history_path(db) == final


def _staged_pair(tmp_path, *, staged_exists=False, final_exists=True):
    from core.downloads import atomic_album_publish as pub
    transfer = tmp_path / "Transfer"
    staged = transfer / pub.STAGING_DIRNAME / "batch1" / "Artist" / "Album" / "01.flac"
    final = transfer / "Artist" / "Album" / "01.flac"
    for flag, path in ((staged_exists, staged), (final_exists, final)):
        if flag:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"AUDIO")
    return str(staged), str(final)


def _repair_db(tmp_path):
    import database.music_database as mdb_mod
    return mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))


def test_repair_repoints_records_naming_a_staging_path(tmp_path):
    """Albums published before the publish repointed the ledger left rows on a
    staging folder that is long gone. The staged path says where the file went."""
    from core.downloads.atomic_recovery import repair_staged_path_records
    db = _repair_db(tmp_path)
    staged, final = _staged_pair(tmp_path)
    _ledger_row(db, staged)
    _history_row(db, staged)

    fixed = repair_staged_path_records(db)

    assert fixed == {"track_downloads": 1, "library_history": 1}
    assert _ledger_path(db) == final
    assert _history_path(db) == final
    assert repair_staged_path_records(db) == {}, "a second run must find nothing to do"


def test_repair_leaves_a_batch_that_is_still_in_staging(tmp_path):
    from core.downloads.atomic_recovery import repair_staged_path_records
    db = _repair_db(tmp_path)
    staged, _final = _staged_pair(tmp_path, staged_exists=True, final_exists=False)
    _ledger_row(db, staged)

    assert repair_staged_path_records(db) == {}
    assert _ledger_path(db) == staged


def test_repair_leaves_a_row_whose_file_is_gone_everywhere(tmp_path):
    from core.downloads.atomic_recovery import repair_staged_path_records
    db = _repair_db(tmp_path)
    staged, _final = _staged_pair(tmp_path, final_exists=False)
    _ledger_row(db, staged)

    assert repair_staged_path_records(db) == {}
    assert _ledger_path(db) == staged
