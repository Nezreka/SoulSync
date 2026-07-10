"""Regression — _fix_unknown_artist must NOT move a media-server file (one that
lives outside the transfer folder) INTO the transfer folder. Same class as #978:
the move destination was built from transfer_folder. For non-transfer libraries
we re-tag + fix DB metadata and leave the file where the media server has it.
"""
import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('a_unknown', 'Unknown Artist', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (1, 'Alb', 'a_unknown', 'test')")
        conn.commit()
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    return db, w


def _insert_track(db, tid, path):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                     "VALUES (?, 'T', ?, 'a_unknown', 1, 'test')", (tid, path))
        conn.commit()


def test_media_server_file_not_moved_into_transfer(tmp_path):
    db, w = _worker(tmp_path)
    ext = tmp_path / "plex" / "Old" / "song.flac"        # OUTSIDE transfer folder
    os.makedirs(ext.parent, exist_ok=True)
    ext.write_bytes(b"\x00" * 32)
    _insert_track(db, 5, str(ext))

    details = {'track_id': 5, 'corrected_artist': 'New Artist',
               'expected_path': 'New Artist/Album/01 - song.flac'}
    w._fix_unknown_artist('track', '5', str(ext), details)

    assert ext.is_file()                                          # stayed put
    assert not (tmp_path / "Transfer" / "New Artist").exists()    # NOT yanked into transfer
    with db._get_connection() as conn:
        assert conn.execute("SELECT file_path FROM tracks WHERE id=5").fetchone()[0] == str(ext)
        assert conn.execute("SELECT a.name FROM tracks t JOIN artists a ON a.id=t.artist_id WHERE t.id=5").fetchone()[0] == 'New Artist'


def test_transfer_file_still_moves(tmp_path):
    db, w = _worker(tmp_path)
    src = os.path.join(w.transfer_folder, "Unknown", "song.flac")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as f:
        f.write(b"\x00" * 32)
    _insert_track(db, 6, src)
    details = {'track_id': 6, 'corrected_artist': 'New Artist',
               'expected_path': 'New Artist/Album/01 - song.flac'}
    w._fix_unknown_artist('track', '6', src, details)
    dst = os.path.join(w.transfer_folder, "New Artist", "Album", "01 - song.flac")
    assert os.path.isfile(dst) and not os.path.exists(src)
