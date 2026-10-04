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


def test_media_server_path_updates_db_by_track_id(tmp_path):
    """The real cross-path case: the DB stores a media-server path that DIFFERS from
    the resolved abs path we move. The DB row must still be updated to the new
    location (by track id, like the live executor) instead of staying stale."""
    db, w = _worker(tmp_path)
    lib = tmp_path / "plex_library"
    src = lib / "Artist" / "Wrong Folder" / "song.flac"
    dst = lib / "Artist" / "Album" / "01 - song.flac"
    os.makedirs(src.parent, exist_ok=True)
    src.write_text("audio")
    # DB stores a DIFFERENT (media-server) path than the resolved abs src.
    _insert_track(db, 20, "/plex/media/Artist/Wrong Folder/song.flac")

    details = {'from': 'x', 'to': 'y', 'from_abs': str(src), 'to_abs': str(dst)}
    res = w._fix_path_mismatch('track', '20', str(src), details)
    assert res['success'] is True, res
    assert dst.is_file() and not src.exists()
    with db._get_connection() as conn:
        # Updated by id despite the stored path not matching the moved path.
        assert conn.execute("SELECT file_path FROM tracks WHERE id=20").fetchone()[0] == os.path.normpath(str(dst))


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


# ── #1504: the fixer must never move a file from one library into another ────

def test_refuses_own_library_file_to_shared_folder(tmp_path, monkeypatch):
    """The #1504 case: a stale finding (written before reorganize learned
    own-library routing) names a shared-folder destination for a file that
    lives in profile 2's own library. The file must NOT move."""
    db, w = _worker(tmp_path)
    src = tmp_path / "lib" / "u2" / "Artist" / "Old" / "s.flac"
    os.makedirs(src.parent, exist_ok=True)
    src.write_text("audio")
    monkeypatch.setattr(
        "core.imports.paths.owning_profile_for_path",
        lambda p, _roots=None: 2 if os.path.normpath(str(p)).startswith(
            os.path.normpath(str(tmp_path / "lib" / "u2")) + os.sep) else None)
    dst = os.path.join(w.transfer_folder, "Artist", "Album", "01 - s.flac")
    _insert_track(db, 30, str(src))
    details = {'from': 'x', 'to': 'y',
               'from_abs': str(src), 'to_abs': dst}
    res = w._fix_path_mismatch('track', '30', str(src), details)
    assert res['success'] is False
    assert 'library boundaries' in res['error']
    assert src.is_file() and not os.path.exists(dst)
    with db._get_connection() as conn:
        assert conn.execute(
            "SELECT file_path FROM tracks WHERE id=30").fetchone()[0] == str(src)


def test_allows_move_within_same_own_library(tmp_path, monkeypatch):
    """Same-owner moves (own→own) proceed exactly as before."""
    own_root = os.path.normpath(str(tmp_path / "lib" / "u2"))
    monkeypatch.setattr(
        "core.imports.paths.owning_profile_for_path",
        lambda p, _roots=None: 2 if os.path.normpath(str(p)).startswith(own_root + os.sep)
        else None)
    db, w = _worker(tmp_path)
    src = tmp_path / "lib" / "u2" / "Artist" / "Old" / "s.flac"
    dst = tmp_path / "lib" / "u2" / "Artist" / "Album" / "01 - s.flac"
    os.makedirs(src.parent, exist_ok=True)
    src.write_text("audio")
    _insert_track(db, 31, str(src))
    details = {'from': 'x', 'to': 'y',
               'from_abs': str(src), 'to_abs': str(dst)}
    res = w._fix_path_mismatch('track', '31', str(src), details)
    assert res['success'] is True, res
    assert dst.is_file() and not src.exists()


def test_refuses_move_between_own_libraries(tmp_path, monkeypatch):
    """A destination in a DIFFERENT profile's own library is also a
    cross-library move — skipped, not moved."""
    u2 = os.path.normpath(str(tmp_path / "lib" / "u2"))
    u3 = os.path.normpath(str(tmp_path / "lib" / "u3"))

    def _owner(path, _roots=None):
        s = os.path.normpath(str(path))
        if s == u2 or s.startswith(u2 + os.sep):
            return 2
        if s == u3 or s.startswith(u3 + os.sep):
            return 3
        return None
    monkeypatch.setattr(
        "core.imports.paths.owning_profile_for_path", _owner)
    db, w = _worker(tmp_path)
    src = tmp_path / "lib" / "u2" / "Artist" / "Old" / "s.flac"
    dst = tmp_path / "lib" / "u3" / "Artist" / "Album" / "01 - s.flac"
    os.makedirs(src.parent, exist_ok=True)
    src.write_text("audio")
    _insert_track(db, 32, str(src))
    details = {'from': 'x', 'to': 'y',
               'from_abs': str(src), 'to_abs': str(dst)}
    res = w._fix_path_mismatch('track', '32', str(src), details)
    assert res['success'] is False
    assert 'library boundaries' in res['error']
    assert src.is_file() and not dst.exists()


def test_refuses_shared_file_into_own_library(tmp_path, monkeypatch):
    """The reverse direction: a shared-folder file must not be moved into a
    profile's own library either."""
    own_root = os.path.normpath(str(tmp_path / "lib" / "u2"))
    monkeypatch.setattr(
        "core.imports.paths.owning_profile_for_path",
        lambda p, _roots=None: 2 if os.path.normpath(str(p)).startswith(own_root + os.sep)
        else None)
    db, w = _worker(tmp_path)
    src = os.path.join(w.transfer_folder, "Artist", "Old", "s.flac")
    dst = tmp_path / "lib" / "u2" / "Artist" / "Album" / "01 - s.flac"
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as f:
        f.write("x")
    _insert_track(db, 33, src)
    details = {'from': 'x', 'to': 'y',
               'from_abs': src, 'to_abs': str(dst)}
    res = w._fix_path_mismatch('track', '33', src, details)
    assert res['success'] is False
    assert 'library boundaries' in res['error']
    assert os.path.isfile(src) and not dst.exists()
