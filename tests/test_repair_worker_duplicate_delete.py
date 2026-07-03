"""Regression tests for duplicate-track file deletion in
``RepairWorker._fix_duplicates`` (Forcebender, Docker).

Reported: "Fix All / Keep Best detects duplicates fine but won't remove them,
not even moving them to /deleted, and it makes NO logs about it."

Root cause: the file-deletion loop deleted the DB row, then tried to delete the
file on disk — but when the stored path didn't resolve to a container-visible
file (Docker volume mapping, same class as #558) OR ``os.remove`` raised a
PUID/PGID permission error, BOTH cases were swallowed silently (a bare
``except OSError: pass`` and an un-logged ``if resolved and os.path.exists``
skip). The op still returned ``success: True``, so the finding resolved and the
bulk-fix path logged nothing. Files were left on disk with zero diagnostics.

The fix logs each failure with a Docker-specific hint and surfaces a
``files_failed`` count in the result/message.
"""

from __future__ import annotations

import logging

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker
from core.repair_jobs.base import skip_deleted_quarantine


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed_parents(db)
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    return db, w


def _seed_parents(db):
    """A single artist + album so track rows satisfy the NOT NULL FKs."""
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'A', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (1, 'Alb', 1, 'test')")
        conn.commit()


def _insert_track(db, tid, title, path):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
            "VALUES (?, ?, ?, 1, 1, ?)",
            (tid, title, path, "test"),
        )
        conn.commit()


def _track_ids(db):
    with db._get_connection() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY id").fetchall()]


def test_removes_real_duplicate_file_and_db_row(tmp_path):
    db, w = _worker(tmp_path)
    keep = tmp_path / "keep.flac"; keep.write_text("k")
    dupe = tmp_path / "dupe.flac"; dupe.write_text("d")
    _insert_track(db, 1, "Song", str(keep))
    _insert_track(db, 2, "Song", str(dupe))

    details = {'tracks': [
        {'id': 1, 'file_path': str(keep), 'bitrate': 1000},
        {'id': 2, 'file_path': str(dupe), 'bitrate': 900},
    ], '_fix_action': '1'}  # keep track 1

    res = w._fix_duplicates('track', '1', str(keep), details)

    assert res['success'] is True
    assert res['files_deleted'] == 1
    assert res['files_failed'] == 0
    assert not dupe.exists()       # moved out of its library location
    assert keep.exists()           # keeper untouched
    assert _track_ids(db) == ['1']   # duplicate DB row gone
    # Recoverable: the duplicate lands in <transfer>/deleted, not hard-deleted.
    quarantined = list((tmp_path / "Transfer" / "deleted").rglob("*.flac"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "d"   # same file, recoverable
    assert 'moved 1 file(s) to the deleted folder' in res['message']


def test_unresolvable_path_logs_and_counts_failure_but_still_cleans_db(tmp_path, caplog):
    """The reported Docker bug: the file can't be located, so previously nothing
    was logged and the file was silently left. Now it's counted + logged."""
    db, w = _worker(tmp_path)
    keep = tmp_path / "keep.flac"; keep.write_text("k")
    missing = "/nonexistent/docker/path/dupe.flac"
    _insert_track(db, 1, "Song", str(keep))
    _insert_track(db, 2, "Song", missing)

    details = {'tracks': [
        {'id': 1, 'file_path': str(keep), 'bitrate': 1000},
        {'id': 2, 'file_path': missing, 'bitrate': 900},
    ], '_fix_action': '1'}

    with caplog.at_level(logging.WARNING):
        res = w._fix_duplicates('track', '1', str(keep), details)

    assert res['success'] is True          # DB cleanup still succeeds
    assert res['files_deleted'] == 0
    assert res['files_failed'] == 1
    assert 'could NOT be removed' in res['message']
    assert _track_ids(db) == ['1']           # DB row removed regardless
    # The previously-silent skip now emits a diagnostic (was the "no logs" complaint).
    assert any('could not locate file to remove' in r.message.lower() for r in caplog.records)


def test_permission_error_logs_puid_hint_and_counts_failure(tmp_path, caplog, monkeypatch):
    """Docker PUID/PGID permission mismatch: the move raises, previously
    swallowed by `except OSError: pass`. Now logged with the PUID/PGID hint."""
    db, w = _worker(tmp_path)
    keep = tmp_path / "keep.flac"; keep.write_text("k")
    dupe = tmp_path / "dupe.flac"; dupe.write_text("d")
    _insert_track(db, 1, "Song", str(keep))
    _insert_track(db, 2, "Song", str(dupe))

    import core.repair_worker as rw

    def _boom(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(rw.shutil, "move", _boom)

    details = {'tracks': [
        {'id': 1, 'file_path': str(keep), 'bitrate': 1000},
        {'id': 2, 'file_path': str(dupe), 'bitrate': 900},
    ], '_fix_action': '1'}

    with caplog.at_level(logging.WARNING):
        res = w._fix_duplicates('track', '1', str(keep), details)

    assert res['files_deleted'] == 0
    assert res['files_failed'] == 1
    assert dupe.exists()                   # mock raised — file not moved
    assert _track_ids(db) == ['1']
    joined = " ".join(r.message for r in caplog.records).lower()
    assert 'failed to move' in joined and ('puid' in joined or 'permission' in joined)


# ---------------------------------------------------------------------------
# skip_deleted_quarantine — transfer-walking repair jobs must not re-scan the
# <transfer>/deleted quarantine, or a just-de-duplicated file reappears as an
# orphan/finding on the next pass.
# ---------------------------------------------------------------------------

def test_skip_deleted_quarantine_prunes_top_level_deleted(tmp_path):
    transfer = str(tmp_path / "Transfer")
    dirs = ["Artist", "deleted", "Other"]
    skip_deleted_quarantine(transfer, dirs, transfer)   # root == transfer
    assert dirs == ["Artist", "Other"]                  # top-level /deleted pruned


def test_skip_deleted_quarantine_leaves_nested_deleted_folder(tmp_path):
    """Anchored to the TOP-LEVEL <transfer>/deleted — a legitimately-named
    'deleted' folder deeper in the library must NOT be pruned."""
    transfer = str(tmp_path / "Transfer")
    nested_root = str(tmp_path / "Transfer" / "Artist" / "Album")
    dirs = ["deleted", "CD1"]
    skip_deleted_quarantine(nested_root, dirs, transfer)
    assert dirs == ["deleted", "CD1"]
