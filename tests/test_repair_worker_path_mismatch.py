"""Regression for #978 — 'Fix All fixes nothing' on path_mismatch findings.

A path_mismatch finding stores display-TRIMMED from/to for the UI, but ALSO the
authoritative absolute paths the preview computed (from_abs/to_abs).
_fix_path_mismatch must move the ABSOLUTE paths so it works for libraries NOT
rooted under transfer_path (Plex/media-server, Docker host<->container splits) —
the case that used to hit the "Path escapes transfer folder" guard and silently
do nothing (both single-fix and Fix All share this handler).
"""
import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'A', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (1, 'Alb', 1, 'test')")
        conn.commit()
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    return db, w


def _insert_track(db, tid, path):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
            "VALUES (?, 'T', ?, 1, 1, 'test')", (tid, path))
        conn.commit()


def test_abs_paths_outside_transfer_are_moved(tmp_path):
    """The reported bug: files live in a media-server library NOT under
    transfer_path. With the authoritative _abs paths, the fix moves the file
    instead of rejecting it as 'escapes transfer folder'."""
    db, w = _worker(tmp_path)
    lib = tmp_path / "plex_library"          # outside w.transfer_folder
    src = lib / "Artist" / "Wrong Folder" / "song.flac"
    dst = lib / "Artist" / "Album" / "01 - song.flac"
    os.makedirs(src.parent, exist_ok=True)
    src.write_text("audio")
    _insert_track(db, 10, str(src))

    details = {
        'from': 'Artist/Wrong Folder/song.flac',   # display-trimmed (unusable as-is here)
        'to': 'Artist/Album/01 - song.flac',
        'from_abs': str(src),
        'to_abs': str(dst),
    }
    res = w._fix_path_mismatch('track', '10', str(src), details)
    assert res['success'] is True, res
    assert dst.is_file() and not src.exists()
    with db._get_connection() as conn:
        assert conn.execute("SELECT file_path FROM tracks WHERE id=10").fetchone()[0] == os.path.normpath(str(dst))


def test_legacy_finding_without_abs_outside_transfer_is_guarded(tmp_path):
    """Old findings (no _abs) whose reconstructed path escapes the transfer folder
    are rejected with a clear 're-scan' message — never silently mangled."""
    _db, w = _worker(tmp_path)
    details = {'from': '/abs/outside/song.flac', 'to': '/abs/outside/new.flac'}
    res = w._fix_path_mismatch('track', '11', '/abs/outside/song.flac', details)
    assert res['success'] is False
    assert 'escapes transfer folder' in res['error']


def test_legacy_finding_under_transfer_still_works(tmp_path):
    """Old findings whose files DO live under transfer_path keep working via the
    reconstruct-from-transfer fallback."""
    db, w = _worker(tmp_path)
    src = os.path.join(w.transfer_folder, "A", "Wrong", "s.flac")
    dst = os.path.join(w.transfer_folder, "A", "Album", "01 - s.flac")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as f:
        f.write("x")
    _insert_track(db, 12, src)
    details = {'from': 'A/Wrong/s.flac', 'to': 'A/Album/01 - s.flac'}   # no _abs
    res = w._fix_path_mismatch('track', '12', src, details)
    assert res['success'] is True, res
    assert os.path.isfile(dst) and not os.path.exists(src)
