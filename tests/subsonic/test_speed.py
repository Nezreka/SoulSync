"""music apps stay fast on a big library: the heavy queries are on indexes
(EXPLAIN QUERY PLAN), and a synthetic library of 120k tracks answers every
list, search and page quickly once its snapshot is built."""

from __future__ import annotations

import json
import time
from contextlib import closing

import pytest

from core.library_scope import library_scope
from core.subsonic import library as L

ARTISTS, ALBUMS_PER, TRACKS_PER = 2000, 6, 10      # 12k albums, 120k tracks


def _plan(conn, sql, params=()):
    return "\n".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall())


@pytest.fixture
def big(mdb):
    with mdb._get_connection() as conn:
        conn.executemany("INSERT INTO lib2_artists (id, name) VALUES (?, ?)",
                         [(a, "Artist %05d" % a) for a in range(1, ARTISTS + 1)])
        albums, tracks, files = [], [], []
        al = t = 0
        for a in range(1, ARTISTS + 1):
            for k in range(ALBUMS_PER):
                al += 1
                albums.append((al, a, "Album %06d" % al, 1960 + al % 60, json.dumps(["Genre%d" % (al % 40)]),
                               "2024-01-01 00:00:%02d" % (al % 60), 1 if al % 97 == 0 else None))
                for n in range(1, TRACKS_PER + 1):
                    t += 1
                    tracks.append((t, al, "Track %07d" % t, n, 200000))
                    files.append((t, "/music/%d/%d.flac" % (al, t), 1000, 900, "flac"))
        conn.executemany("INSERT INTO lib2_albums (id, primary_artist_id, title, year, genres, added_at, explicit) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", albums)
        conn.executemany("INSERT INTO lib2_tracks (id, album_id, title, track_number, duration) "
                         "VALUES (?, ?, ?, ?, ?)", tracks)
        conn.executemany("INSERT INTO lib2_track_files (track_id, path, size, bitrate, format, is_primary, "
                         "file_state) VALUES (?, ?, ?, ?, ?, 1, 'active')", files)
        conn.commit()
        conn.execute("ANALYZE")
    # the songs search index, as the server builds it in the background
    from core.library2.song_search import rebuild
    assert rebuild(mdb)
    return mdb


def test_the_heavy_queries_use_indexes(big):
    with closing(big._get_connection()) as conn, library_scope("shared"):
        snap = _plan(conn, L.snapshot_sql(True, True))
        # one pass over the albums is the point; everything inside it seeks
        assert "USING INDEX idx_lib2_tracks_album" in snap or "USING COVERING INDEX idx_lib2_tracks_album" in snap
        assert "idx_lib2_track_files" in snap
        assert "SCAN t " not in snap + " " and "SCAN owned_f" not in snap
        lib = L.Library(conn, db_key=str(big.database_path), scope="shared", hide_explicit=True)
        rows = _plan(conn, f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                           f"WHERE t.album_id IN (SELECT value FROM json_each(?)) AND {L.owned_track_sql('t')}"
                           f"{lib._hidden()}", ("[1,2]",))
        assert "idx_lib2_tracks_album" in rows and "SCAN t" not in rows
        by_title = _plan(conn, f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                               f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
                               f"WHERE LOWER(t.title) = LOWER(?) AND {L.owned_track_sql('t')}", ("x",))
        assert "idx_lib2_tracks_title_lower" in by_title
        one = _plan(conn, "SELECT t.id FROM lib2_tracks t WHERE t.id IN (SELECT value FROM json_each(?))", ("[1]",))
        assert "INTEGER PRIMARY KEY" in one


def test_recent_and_frequent_ride_the_listening_index(mdb):
    with closing(mdb._get_connection()) as conn:
        plan = _plan(conn, "SELECT t.album_id FROM listening_history lh JOIN lib2_tracks t ON t.id = lh.lib2_track_id "
                           "WHERE lh.lib2_track_id IS NOT NULL AND lh.profile_id = 1 GROUP BY t.album_id "
                           "ORDER BY COUNT(*) DESC LIMIT 2000")
    assert "USING" in plan and "idx_listening" in plan


def _timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


def test_a_big_library_answers_quickly(big):
    with closing(big._get_connection()) as conn, library_scope("shared"):
        lib = L.Library(conn, db_key=str(big.database_path), scope="shared", hide_explicit=True)
        snap, built = _timed(lib.snap)
        assert len(snap.albums) == ARTISTS * ALBUMS_PER - ARTISTS * ALBUMS_PER // 97
        assert built < 30, built            # once per change, on a loaded shared machine
        checks = {
            "artists": lambda: lib.artist_index(),
            "a-z page deep": lambda: lib.album_list("alphabeticalByName", size=500, offset=11000),
            "by artist": lambda: lib.album_list("alphabeticalByArtist", size=500, offset=5000),
            "newest": lambda: lib.album_list("newest", size=50, offset=0),
            "by genre": lambda: lib.album_list("byGenre", size=50, offset=100, genre="genre7"),
            "genres": lambda: lib.genres(),
            "random songs": lambda: lib.random_songs(50),
            "sync page deep": lambda: lib.all_songs_page(110000, 500),
            "genre songs": lambda: lib.genre_songs("Genre3", 1000, 500),
            "search": lambda: lib.search("album 0004", artist_count=20, artist_offset=0, album_count=20,
                                         album_offset=0, song_count=20, song_offset=0),
            "songs page": lambda: lib.songs(list(range(50000, 50500))),
            "find by name": lambda: lib.find_track("Track 0060000", "Artist 01000"),
        }
        for name, fn in checks.items():
            fn()   # first call builds any cached order
            out, took = _timed(fn)
            assert took < 2.0, (name, took)
        assert len(lib.all_songs_page(110000, 500)) == 500
        assert lib.find_track("Track 0060000", "Artist 01000") == 60000
