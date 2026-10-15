"""every enrichment worker's "next pending item" reads one index entry.

the workers ask `WHERE <svc>_match_status IS NULL ORDER BY id LIMIT 1`. on an
index of the status alone sqlite sorted every pending row to return one:
120-240 ms per item while a big library's queue is full, for each of 13
workers, for the weeks initial enrichment takes. on (status, id) the first
index entry is the answer, same rows.
"""

import re
import sqlite3

import pytest

from database.music_database import MusicDatabase, _planner_stats_threads


def _status_indexes(path):
    c = sqlite3.connect(str(path))
    rows = c.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' "
        "AND tbl_name IN ('artists', 'albums', 'tracks') AND sql LIKE '%match_status%'").fetchall()
    c.close()
    return rows


def test_a_fresh_db_builds_them_on_status_and_id(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    rows = _status_indexes(db.database_path)
    assert len(rows) >= 35, rows
    for name, _table, sql in rows:
        assert re.search(r"\(\w+_match_status, id\)\s*$", sql), (name, sql)
    c = sqlite3.connect(str(db.database_path))
    assert MusicDatabase._match_status_indexes_to_upgrade(c.cursor()) == []
    c.close()


@pytest.mark.parametrize("table", ["artists", "albums", "tracks"])
def test_pending_lookups_need_no_sort(tmp_path, table):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    c = sqlite3.connect(str(db.database_path))
    for name, tbl, sql in _status_indexes(db.database_path):
        if tbl != table:
            continue
        col = re.search(r"\((\w+_match_status), id\)", sql).group(1)
        plan = [r[3] for r in c.execute(
            f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE {col} IS NULL AND id IS NOT NULL "
            f"ORDER BY id ASC LIMIT 1")]
        assert not any("TEMP B-TREE" in s for s in plan), (name, plan)
    c.close()


def _make_old_shape(path, names):
    c = sqlite3.connect(str(path))
    for name, table, sql in _status_indexes(path):
        if name in names:
            col = re.search(r"\((\w+_match_status), id\)", sql).group(1)
            c.execute(f"DROP INDEX {name}")
            c.execute(f"CREATE INDEX {name} ON {table} ({col})")
    c.commit()
    c.close()


def _assert_composite(path, names):
    sqls = {n: q for n, _t, q in _status_indexes(path)}
    for name in names:
        assert re.search(r"\(\w+_match_status, id\)\s*$", sqls[name]), (name, sqls[name])
    c = sqlite3.connect(str(path))
    assert MusicDatabase._match_status_indexes_to_upgrade(c.cursor()) == []
    c.close()


@pytest.mark.parametrize("sql", [
    "CREATE INDEX idx_x ON tracks (audiodb_match_status)",
    'CREATE INDEX "idx_x" ON "tracks" (audiodb_match_status)',
    "CREATE INDEX idx_x ON tracks(audiodb_match_status)",
])
def test_the_old_shape_is_recognised_however_it_was_written(sql):
    assert MusicDatabase._SINGLE_STATUS_INDEX.match(sql)


@pytest.mark.parametrize("sql", [
    "CREATE INDEX idx_x ON tracks (audiodb_match_status, id)",
    "CREATE INDEX idx_x ON tracks (repair_status)",
    "CREATE INDEX idx_x ON liked_artists_pool (profile_id, match_status)",
])
def test_anything_else_is_left_alone(sql):
    assert not MusicDatabase._SINGLE_STATUS_INDEX.match(sql)


def test_an_existing_small_db_is_upgraded_inline(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    old = {"idx_tracks_audiodb_status", "idx_albums_mb_status", "idx_artists_deezer_status"}
    _make_old_shape(db.database_path, old)
    c = sqlite3.connect(str(db.database_path))
    assert {u[0] for u in MusicDatabase._match_status_indexes_to_upgrade(c.cursor())} == old
    c.close()
    _planner_stats_threads.pop(str(db.database_path), None)
    db._kick_planner_stats_thread()
    assert str(db.database_path) not in _planner_stats_threads   # no thread for a small db
    _assert_composite(db.database_path, old)


def test_an_existing_big_db_is_upgraded_in_the_background_and_analyzed(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO artists (id, name) VALUES ('1', 'A')")
    c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('10', '1', 'B')")
    c.executemany("INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, '10', '1', 't')",
                  [(str(5000 + i),) for i in range(2000)])
    c.commit()
    c.close()
    _make_old_shape(db.database_path, {"idx_tracks_audiodb_status"})
    c = sqlite3.connect(str(db.database_path))
    assert [u[0] for u in MusicDatabase._match_status_indexes_to_upgrade(c.cursor())] == ["idx_tracks_audiodb_status"]
    c.close()
    db._kick_planner_stats_thread()
    t = _planner_stats_threads.get(str(db.database_path))
    assert t is not None
    t.join(timeout=60)
    _assert_composite(db.database_path, {"idx_tracks_audiodb_status"})
    c = sqlite3.connect(str(db.database_path))
    assert c.execute("SELECT 1 FROM sqlite_stat1 WHERE idx = 'idx_tracks_audiodb_status'").fetchone()
    c.close()


def test_the_audiodb_worker_reads_its_queue_without_sorting(tmp_path, monkeypatch):
    """the call site: the worker's own query, through its own method."""
    from core.audiodb_worker import AudioDBWorker

    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO artists (id, name, audiodb_match_status) VALUES ('1', 'A', 'matched')")
    c.execute("INSERT INTO albums (id, artist_id, title, audiodb_match_status) VALUES ('10', '1', 'B', 'matched')")
    c.executemany("INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, '10', '1', 't')",
                  [(str(5000 + i),) for i in range(50)])
    c.commit()
    c.close()

    plans = []

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if "audiodb_match_status IS NULL" in sql and "FROM tracks" in sql:
                plans.append([r[3] for r in super().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()])
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    def connect():
        conn = sqlite3.connect(str(db.database_path), factory=Conn)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(db, "_get_connection", connect)
    w = AudioDBWorker.__new__(AudioDBWorker)
    w.db = db
    item = w._get_next_item()
    assert item and item["type"] == "track" and item["id"] == "5000"
    assert plans, "the track queue query never ran"
    for plan in plans:
        assert any("idx_tracks_audiodb_status" in s for s in plan), plan
        assert not any("TEMP B-TREE" in s for s in plan), plan


def test_a_fresh_db_never_pays_for_a_conversion(tmp_path, monkeypatch):
    """the indexes are created in the new shape, so the boot step finds
    nothing to rebuild (every test db and every new install would otherwise
    pay ~37 drop/create)."""
    calls = []
    monkeypatch.setattr(MusicDatabase, "upgrade_match_status_indexes",
                        lambda self, upgrades: calls.append(list(upgrades)))
    MusicDatabase(database_path=str(tmp_path / "fresh.db"))
    assert calls == []
