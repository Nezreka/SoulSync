"""sqlite gets statistics for every index that matters, and keeps them fresh.

the planner stats only ever got refreshed after a one-time norm backfill. an
index added after that (idx_tracks_source_owner, sept 19) had none, and an
index without stats looks like it narrows an equality to ~10 rows. so
`server_source = 'plex'` (every row on a one-server install) looked like the
best way to find a track, and every title search walked all 300k tracks:
1.4 s per missing track in a watchlist scan, 32 ms once analyzed.
"""

import inspect
import sqlite3

import pytest

from database.music_database import MusicDatabase, _planner_stats_threads


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, name_norm, name_key, server_source) VALUES ('1', 'Artist One', 'artist one', 'artistone', 'plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, title_norm, server_source) VALUES ('10', '1', 'Album', 'album', 'plex')")
    c.executemany(
        "INSERT INTO tracks (id, album_id, artist_id, title, title_norm, track_artist_norm, server_source) "
        "VALUES (?, '10', '1', ?, ?, 'artist one', 'plex')",
        [(str(1000 + i), f"Song {i}", f"song {i}") for i in range(3000)])
    c.commit()
    c.close()
    return d


def _forget_stats_for(db, index):
    c = sqlite3.connect(str(db.database_path))
    c.execute("ANALYZE tracks")
    c.execute("DELETE FROM sqlite_stat1 WHERE idx = ?", (index,))
    c.commit()
    c.close()


def _title_search_plans(db, monkeypatch):
    plans = []

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            flat = " ".join(sql.split())
            if "tracks.title_norm" in flat and flat.startswith("SELECT"):
                plans.append([r[3] for r in super().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()])
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    from core.text.normalize import normalize_for_comparison as nfc

    real = MusicDatabase._get_connection

    def connect(self):
        c = sqlite3.connect(str(self.database_path), factory=Conn)
        c.row_factory = sqlite3.Row
        c.create_function("norm_text", 1, lambda x: nfc(x) if x else "", deterministic=True)
        c.create_function("unidecode_lower", 1, lambda x: x.lower() if x else "")
        return c

    monkeypatch.setattr(MusicDatabase, "_get_connection", connect)
    db.check_track_exists("A Song That Is Not Here", "Artist One", confidence_threshold=0.7, server_source="plex")
    monkeypatch.setattr(MusicDatabase, "_get_connection", real)
    return plans


def test_an_unanalyzed_index_is_found(db):
    _forget_stats_for(db, "idx_tracks_source_owner")
    c = sqlite3.connect(str(db.database_path))
    assert "tracks" in MusicDatabase._tables_missing_planner_stats(c.cursor())
    c.close()


def test_small_tables_are_left_alone(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "small.db"))
    c = sqlite3.connect(str(d.database_path))
    assert MusicDatabase._tables_missing_planner_stats(c.cursor()) == []
    c.close()


def test_the_boot_kick_analyzes_it_and_title_searches_stop_walking_server_source(db, monkeypatch):
    _forget_stats_for(db, "idx_tracks_source_owner")
    before = _title_search_plans(db, monkeypatch)
    # the bug, reproduced: the unanalyzed index drives the search
    assert any(any("idx_tracks_source_owner" in s for s in p) for p in before), before

    db._kick_planner_stats_thread()
    t = _planner_stats_threads.get(str(db.database_path))
    assert t is not None
    t.join(timeout=30)
    assert not t.is_alive()

    c = sqlite3.connect(str(db.database_path))
    assert MusicDatabase._tables_missing_planner_stats(c.cursor()) == []
    c.close()
    after = _title_search_plans(db, monkeypatch)
    assert after
    for p in after:
        assert not any("idx_tracks_source_owner" in s for s in p), p


def test_nothing_to_analyze_starts_no_thread(db):
    db.refresh_planner_stats()
    _planner_stats_threads.pop(str(db.database_path), None)
    db._kick_planner_stats_thread()
    assert str(db.database_path) not in _planner_stats_threads


def test_refresh_is_sampled():
    src = inspect.getsource(MusicDatabase.refresh_planner_stats)
    assert 'conn.execute(f"PRAGMA analysis_limit = {_PLANNER_ANALYSIS_LIMIT}")' in src


def test_init_kicks_the_check():
    src = inspect.getsource(MusicDatabase._initialize_database_once)
    assert "\n        self._kick_planner_stats_thread()\n" in src.replace("\r\n", "\n")


def test_a_finished_scan_refreshes_the_stats():
    from core.database_update_worker import DatabaseUpdateWorker

    calls = []

    class FakeDb:
        def ensure_norm_backfilled(self):
            calls.append("norm")

        def refresh_planner_stats(self):
            calls.append("stats")

    w = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    w.callbacks = {"finished": [lambda *a: calls.append("finished")]}
    w.post_scan_hook = None
    w.database = FakeDb()
    w._emit_finished(0, 0, 0, 0, 0)
    assert calls == ["norm", "stats", "finished"]


def test_a_without_rowid_table_is_not_flagged_forever(tmp_path):
    """sqlite files a WITHOUT ROWID table's primary key stats under the table
    name, so its sqlite_autoindex never shows up in sqlite_stat1. reading that
    as unanalyzed re-analyzed track_credits on every boot."""
    c = sqlite3.connect(str(tmp_path / "wr.db"))
    c.execute("CREATE TABLE credits (track_id TEXT, name TEXT, PRIMARY KEY (track_id, name)) WITHOUT ROWID")
    c.execute("CREATE INDEX idx_credits_name ON credits (name)")
    c.executemany("INSERT INTO credits VALUES (?, ?)", [(str(i), f"n{i % 50}") for i in range(2000)])
    c.commit()
    assert MusicDatabase._tables_missing_planner_stats(c.cursor()) == ["credits"]
    c.execute("ANALYZE credits")
    c.commit()
    assert MusicDatabase._tables_missing_planner_stats(c.cursor()) == []
    c.close()


def test_a_worker_without_a_database_finishes_quietly(monkeypatch):
    import core.database_update_worker as mod
    from core.database_update_worker import DatabaseUpdateWorker

    warned = []
    monkeypatch.setattr(mod.logger, "warning", lambda *a, **k: warned.append(a))
    w = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    w.callbacks = {"finished": []}
    w.post_scan_hook = None
    w._emit_finished(0, 0, 0, 0, 0)
    assert warned == []
