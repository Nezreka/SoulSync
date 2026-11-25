"""#2289: a library moved between mounts keeps its old records forever.

124 songs, 681 file records: each song recorded once where it is and several
times where it used to be. the scan settles such a record when it finds the
same file on disk under another record, but only did so after the outage
check had passed, and 557 absent records out of 681 read as an outage. so
nothing was settled, on any scan, and the duplicate and re-tag jobs kept
reading records of files that exist nowhere.
"""

from __future__ import annotations

from contextlib import closing

from core.library2 import scan
from database.music_database import MusicDatabase

SONGS = 60          # over MASS_MISSING_MIN on their own
OLD_MOUNTS = 2      # two stale records per song: far over the 25% share


def _library(tmp_path, *, files_on_disk=True):
    db = MusicDatabase(str(tmp_path / "music.db"))
    music = tmp_path / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    with closing(db._get_connection()) as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')", (artist,)).lastrowid
        for n in range(1, SONGS + 1):
            name = f"{n:02d} - Song {n}.flac"
            size = 1000 + n
            if files_on_disk:
                (music / name).write_bytes(b"x" * size)
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,?)",
                (album, f"Song {n}", n)).lastrowid
            conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                         (track, str(music / name), size))
            for mount in range(OLD_MOUNTS):
                conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                             (track, f"/old-mount-{mount}/Artist/Album/{name}", size))
        conn.commit()
    return db


def _states(db):
    with closing(db._get_connection()) as conn:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT path, COALESCE(file_state,'active') FROM lib2_track_files")]


def test_old_records_are_settled_however_many_there_are(tmp_path):
    db = _library(tmp_path)
    stats = scan.rescan_files(db)
    states = _states(db)
    live = [s for p, s in states if not p.startswith("/old-mount-")]
    old = [s for p, s in states if p.startswith("/old-mount-")]
    assert live == ["active"] * SONGS
    assert stats["moved_records"] == SONGS * OLD_MOUNTS
    assert "active" not in old, "a record of a path that is gone is still a live file"
    assert not stats.get("mass_missing_deferred")


def test_a_real_outage_still_settles_nothing(tmp_path):
    """nothing on disk at all: no record has a twin that is present, so none
    is settled and the outage check still holds everything back"""
    db = _library(tmp_path, files_on_disk=False)
    stats = scan.rescan_files(db)
    assert stats["moved_records"] == 0
    assert stats["mass_missing_deferred"] == SONGS * (OLD_MOUNTS + 1)
    assert {s for _p, s in _states(db)} == {"active"}
