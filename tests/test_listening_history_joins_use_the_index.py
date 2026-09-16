"""every join from listening_history to the catalogue must search a key, not scan.

Upstream joined ``db_track_id`` (INTEGER) to ``tracks.id`` (TEXT plex rating
keys); sqlite gave the TEXT column numeric affinity, the primary-key index
could not be used, and every history row scanned the whole table:
/api/stats/recent took 54 s per dashboard load on 300k tracks (407 s cold),
and the two genre queries on the stats page did the same over 87k rows. Its
fix was CAST(db_track_id AS TEXT).

This branch joins ``lib2_track_id`` to ``lib2_tracks.id`` instead -- INTEGER
on both sides, so the affinity trap does not exist and no CAST is needed. The
guarantee is still worth pinning, so these read the query plan: whichever way
the join is spelled, it must remain a search.
"""

import sqlite3

import pytest

import core.stats.queries as queries
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    conn = d._get_connection()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO lib2_artists (id, name, name_key, genres)"
                  " VALUES (1, 'Oasis', 'oasis', '[\"rock\"]')")
        c.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url)"
                  " VALUES (10, 1, 'Definitely Maybe', '/library/art/10')")
        c.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (100, 10, 'Live Forever')")
        c.execute("INSERT INTO listening_history (track_id, title, artist, album, played_at,"
                  " lib2_track_id, server_source)"
                  " VALUES ('100','Live Forever','Oasis','Definitely Maybe',"
                  "         '2026-09-15 10:00:00', 100, 'plex')")
        c.execute("INSERT INTO listening_history (track_id, title, artist, album, played_at,"
                  " lib2_track_id, server_source)"
                  " VALUES ('x','Unmatched','Nobody','None','2026-09-15 09:00:00', NULL, 'plex')")
        conn.commit()
    finally:
        conn.close()
    return d


def _plans_for_statements(db, monkeypatch, run):
    """collect the query plan of every statement that touches tracks while `run` executes."""
    plans = []
    real = MusicDatabase._get_connection

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if "lib2_tracks" in sql and "listening_history" in sql and not sql.lstrip().upper().startswith("EXPLAIN"):
                plans.append((sql, super().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()))
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    def conn(self):
        c = sqlite3.connect(str(self.database_path), factory=Conn)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(MusicDatabase, "_get_connection", conn)
    run()
    return plans


def _asserts_no_track_scan(plans):
    assert plans, "no statement joined listening_history to the catalogue"
    for sql, plan in plans:
        text = " | ".join(str(tuple(p)) for p in plan)
        # INTEGER PRIMARY KEY is the rowid, so sqlite reports the search as
        # "USING INTEGER PRIMARY KEY" rather than naming an index
        assert "SEARCH t USING INTEGER PRIMARY KEY" in text, f"tracks scanned:\n{sql}\n{text}"
        assert "SCAN t" not in text, text


def test_recent_tracks_joins_art_through_the_primary_key(db, monkeypatch):
    out = {}
    plans = _plans_for_statements(db, monkeypatch, lambda: out.setdefault("rows", queries.get_recent_tracks(db, 25, None)))
    _asserts_no_track_scan(plans)
    rows = out["rows"]
    assert rows[0]["title"] == "Live Forever" and rows[0]["image_url"] == "/library/art/10"
    assert str(rows[0]["artist_db_id"]) == "1"
    assert rows[1]["title"] == "Unmatched" and rows[1]["image_url"] is None


def test_genre_queries_join_through_the_primary_key(db, monkeypatch):
    out = {}
    plans = _plans_for_statements(db, monkeypatch, lambda: out.setdefault("g", (db.get_genre_breakdown("all"), db.get_genre_own_vs_play("all"))))
    _asserts_no_track_scan(plans)
    breakdown, _own = out["g"]
    assert breakdown, "the matched play should count toward a genre"
