"""every join from listening_history.db_track_id to tracks.id must search the
primary key, not scan tracks.

tracks.id is TEXT (plex rating keys) and db_track_id is INTEGER. sqlite
applies numeric affinity to the TEXT column in that comparison, so the
primary-key index cannot be used and each history row scans the whole
tracks table: /api/stats/recent took 54 s per dashboard load on 300k
tracks (407 s cold), and the two genre queries on the stats page did the
same over 87k history rows. CAST(db_track_id AS TEXT) makes it a search.
these read the query plan so the regression cannot come back silently.
"""

import sqlite3

import pytest

import core.stats.queries as queries
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, server_source, genres) VALUES ('1', 'Oasis', 'plex', '[\"rock\"]')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source, thumb_url) VALUES ('10', '1', 'Definitely Maybe', 'plex', '/library/art/10')")
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source) VALUES ('100', '10', '1', 'Live Forever', 'plex')")
    c.execute("INSERT INTO listening_history (track_id, title, artist, album, played_at, db_track_id, server_source) VALUES ('100', 'Live Forever', 'Oasis', 'Definitely Maybe', '2026-09-15 10:00:00', 100, 'plex')")
    c.execute("INSERT INTO listening_history (track_id, title, artist, album, played_at, db_track_id, server_source) VALUES ('x', 'Unmatched', 'Nobody', 'None', '2026-09-15 09:00:00', NULL, 'plex')")
    c.commit()
    c.close()
    return d


def _plans_for_statements(db, monkeypatch, run):
    """collect the query plan of every statement that touches tracks while `run` executes."""
    plans = []
    real = MusicDatabase._get_connection

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if "tracks" in sql and "listening_history" in sql and not sql.lstrip().upper().startswith("EXPLAIN"):
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
    assert plans, "no statement joined listening_history to tracks"
    for sql, plan in plans:
        text = " | ".join(str(tuple(p)) for p in plan)
        assert "SEARCH t USING INDEX sqlite_autoindex_tracks_1" in text, f"tracks scanned:\n{sql}\n{text}"
        assert "SCAN t" not in text, text


def test_recent_tracks_joins_art_through_the_primary_key(db, monkeypatch):
    out = {}
    plans = _plans_for_statements(db, monkeypatch, lambda: out.setdefault("rows", queries.get_recent_tracks(db, 25, None)))
    _asserts_no_track_scan(plans)
    rows = out["rows"]
    assert rows[0]["title"] == "Live Forever" and rows[0]["image_url"] == "/library/art/10" and rows[0]["artist_db_id"] == "1"
    assert rows[1]["title"] == "Unmatched" and rows[1]["image_url"] is None


def test_genre_queries_join_through_the_primary_key(db, monkeypatch):
    out = {}
    plans = _plans_for_statements(db, monkeypatch, lambda: out.setdefault("g", (db.get_genre_breakdown("all"), db.get_genre_own_vs_play("all"))))
    _asserts_no_track_scan(plans)
    breakdown, _own = out["g"]
    assert breakdown, "the matched play should count toward a genre"
