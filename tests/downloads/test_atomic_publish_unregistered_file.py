"""an album publish holds back only when a row would dangle.

v2 made a zero repoint roll the whole album back on every install, so one
file the import never registered hid every good track in staging. a zero is
only dangerous when a row names the staged file in another spelling: that
row would point into a folder about to be deleted (L2-002).
"""

from __future__ import annotations

from core.downloads import atomic_recovery
from database.music_database import MusicDatabase


def _db(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('A')").lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'B')",
                             (artist,)).lastrowid
        track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'S')",
                             (album,)).lastrowid
        conn.commit()
    return db, track


def test_a_file_no_row_names_publishes(tmp_path):
    db, _ = _db(tmp_path)
    update = atomic_recovery.make_db_path_updater(db)
    assert update("/lib/.staging/B/A/01.flac", "/lib/A/B/01.flac") is None


def test_a_row_in_another_spelling_still_rolls_back(tmp_path):
    db, track = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                     (track, "/LIB/.staging/B/A/01.flac"))
        conn.commit()
    update = atomic_recovery.make_db_path_updater(db)
    assert update("/lib/.staging/B/A/01.flac", "/lib/A/B/01.flac") == 0


def test_a_registered_file_repoints(tmp_path):
    db, track = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                     (track, "/lib/.staging/B/A/01.flac"))
        conn.commit()
    update = atomic_recovery.make_db_path_updater(db)
    assert update("/lib/.staging/B/A/01.flac", "/lib/A/B/01.flac") == 1
    with db._get_connection() as conn:
        assert conn.execute("SELECT path FROM lib2_track_files").fetchone()[0] == "/lib/A/B/01.flac"
