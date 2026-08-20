"""Regression for #978 — 'Fix All fixes nothing' on path_mismatch findings.

A path_mismatch finding stores display-TRIMMED from/to for the UI, but ALSO the
authoritative absolute paths the preview computed (from_abs/to_abs).
_fix_path_mismatch must move the ABSOLUTE paths so it works for libraries NOT
rooted under transfer_path (Plex/media-server, Docker host<->container splits) —
the case that used to hit the "Path escapes transfer folder" guard and silently
do nothing (both single-fix and Fix All share this handler).

Subjects are ``lib2:<id>``. A bare integer is a LEGACY back-reference by
contract (T-12), and this handler refuses it — a finding persisted before its
job moved to native subjects would otherwise mutate the legacy twin of a track
whose real row is in ``lib2_tracks``. The last test pins that refusal.
"""
import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (1, 'A', 'a')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, origin)"
                     " VALUES (1, 1, 'Alb', 'library')")
        conn.commit()
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    return db, w


def _insert_track(db, tid, path):
    """A catalogue track and its file. The path lives on the file row (ADR-03),
    which is what the fix has to re-point after a move."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, 1, 'T')", (tid,))
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
            (tid, path))
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
    res = w._fix_path_mismatch('track', 'lib2:10', str(src), details)
    assert res['success'] is True, res
    assert dst.is_file() and not src.exists()
    with db._get_connection() as conn:
        assert conn.execute("SELECT path FROM lib2_track_files WHERE track_id=10").fetchone()[0] == os.path.normpath(str(dst))


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
    res = w._fix_path_mismatch('track', 'lib2:20', str(src), details)
    assert res['success'] is True, res
    assert dst.is_file() and not src.exists()
    with db._get_connection() as conn:
        # Updated by id despite the stored path not matching the moved path.
        assert conn.execute("SELECT path FROM lib2_track_files WHERE track_id=20").fetchone()[0] == os.path.normpath(str(dst))


def test_legacy_finding_without_abs_outside_transfer_is_guarded(tmp_path):
    """Old findings (no _abs) whose reconstructed path escapes the transfer folder
    are rejected with a clear 're-scan' message — never silently mangled."""
    _db, w = _worker(tmp_path)
    details = {'from': '/abs/outside/song.flac', 'to': '/abs/outside/new.flac'}
    res = w._fix_path_mismatch('track', 'lib2:11', '/abs/outside/song.flac', details)
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
    res = w._fix_path_mismatch('track', 'lib2:12', src, details)
    assert res['success'] is True, res
    assert os.path.isfile(dst) and not os.path.exists(src)


def test_a_bare_integer_subject_is_refused_as_pre_library_v2(tmp_path):
    """The contract itself. A bare id reaching this handler means the finding
    predates native subjects; applying it would move a file on behalf of the
    LEGACY twin of some other track. It is refused and flagged stale so the
    next scan can raise it against the right row."""
    db, w = _worker(tmp_path)
    src = os.path.join(w.transfer_folder, "A", "Wrong", "s.flac")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as f:
        f.write("x")
    _insert_track(db, 13, src)

    res = w._fix_path_mismatch(
        'track', '13', src, {'from': 'A/Wrong/s.flac', 'to': 'A/Album/01 - s.flac'})

    assert res['success'] is False
    assert res['stale_subject'] is True
    assert os.path.isfile(src), 'a refused finding must not have touched the file'
