"""A pooled connection must behave like the fresh connection every caller used to get."""

from __future__ import annotations

import gc
import sqlite3
import threading

import pytest

from database import connection_pool as cp
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "pool.db"))


def _hits():
    return cp.stats["hits"]


def test_close_then_acquire_reuses_the_connection(db):
    c1 = db._get_connection()
    raw1 = c1._slot.raw
    c1.close()
    before = _hits()
    c2 = db._get_connection()
    assert c2._slot.raw is raw1
    assert _hits() == before + 1
    c2.close()


def test_two_checkouts_at_once_are_two_connections(db):
    a, b = db._get_connection(), db._get_connection()
    assert a._slot.raw is not b._slot.raw
    a.execute("CREATE TABLE IF NOT EXISTS t1(x)")
    a.commit()
    b.execute("INSERT INTO t1 VALUES (1)")           # b's write is not a's transaction
    assert a.execute("SELECT COUNT(*) FROM t1").fetchone()[0] == 0
    b.commit()
    a.close()
    b.close()


def test_use_after_close_raises_like_a_closed_connection(db):
    c = db._get_connection()
    c.close()
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")
    c.close()                                          # closing twice stays harmless
    other = db._get_connection()                        # and must not touch the reused one
    assert other.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 2")
    other.close()


def test_uncommitted_work_is_discarded_on_close(db):
    c = db._get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS t2(x)")
    c.commit()
    c.execute("INSERT INTO t2 VALUES (1)")
    c.close()                                          # closing a connection rolled this back
    c2 = db._get_connection()
    assert c2.execute("SELECT COUNT(*) FROM t2").fetchone()[0] == 0
    assert not c2.in_transaction
    c2.close()


def test_with_commits_and_does_not_close(db):
    c = db._get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS t3(x)")
    with c as same:
        assert same is c                               # the proxy, never the raw connection
        c.execute("INSERT INTO t3 VALUES (1)")
    assert c.execute("SELECT COUNT(*) FROM t3").fetchone()[0] == 1   # still usable
    c.close()
    with pytest.raises(ValueError):
        with db._get_connection() as conn:
            conn.execute("INSERT INTO t3 VALUES (2)")
            raise ValueError("boom")
    gc.collect()
    c3 = db._get_connection()
    assert c3.execute("SELECT COUNT(*) FROM t3").fetchone()[0] == 1   # rolled back
    c3.close()


def test_a_never_closed_connection_comes_back_on_gc(db):
    c = db._get_connection()
    raw = c._slot.raw
    c.execute("SELECT 1").fetchone()
    del c
    gc.collect()
    c2 = db._get_connection()
    assert c2._slot.raw is raw
    c2.close()


def test_a_live_cursor_keeps_its_connection_out_of_the_pool(db):
    c = db._get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS t4(x)")
    c.executemany("INSERT INTO t4 VALUES (?)", [(i,) for i in range(5)])
    c.commit()
    raw = c._slot.raw
    cur = c.execute("SELECT x FROM t4 ORDER BY x")
    del c                                              # gc'd with the cursor still in use
    gc.collect()
    other = db._get_connection()
    assert other._slot.raw is not raw
    assert [r[0] for r in cur] == [0, 1, 2, 3, 4]       # the cursor still works
    other.close()


def test_state_a_caller_changed_is_reset(db):
    c = db._get_connection()
    c.row_factory = None
    c.execute("PRAGMA foreign_keys = OFF")
    c.close()
    c2 = db._get_connection()
    assert c2.row_factory is sqlite3.Row
    assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c2.execute("SELECT 1 AS a").fetchone()["a"] == 1
    c2.close()


def test_a_connection_can_be_reused_by_another_thread(db):
    c = db._get_connection()
    raw = c._slot.raw
    c.close()
    seen = []

    def work():
        t = db._get_connection()
        seen.append(t._slot.raw)
        assert t.execute("SELECT 1").fetchone()[0] == 1
        t.close()

    th = threading.Thread(target=work)
    th.start()
    th.join()
    assert seen == [raw]


def test_concurrent_checkouts_are_distinct_connections(db):
    barrier = threading.Barrier(4)
    seen, errors = [], []

    def work():
        try:
            c = db._get_connection()
            barrier.wait(timeout=10)                  # all four hold one at the same time
            seen.append(id(c._slot.raw))
            c.execute("SELECT 1").fetchone()
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ths = [threading.Thread(target=work) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errors
    assert len(set(seen)) == 4


def test_the_pool_is_bounded(db, monkeypatch):
    monkeypatch.setattr(cp, "MAX_IDLE", 2)
    held = [db._get_connection() for _ in range(5)]
    raws = [c._slot.raw for c in held]
    for c in held:
        c.close()
    with cp._lock:
        assert len(cp._idle[str(db.database_path)]) == 2
    closed = 0
    for r in raws:
        try:
            r.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            closed += 1
    assert closed == 3                                   # the overflow was really closed


def test_a_replaced_database_file_is_reopened(tmp_path):
    path = tmp_path / "swap.db"
    db = MusicDatabase(str(path))
    c = db._get_connection()
    raw = c._slot.raw
    c.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            (tmp_path / f"swap.db{suffix}").unlink()
        except FileNotFoundError:
            pass
    fresh = sqlite3.connect(path)                        # a restored file: new inode
    fresh.execute("CREATE TABLE marker(x)")
    fresh.commit()
    fresh.close()
    c2 = db._get_connection()
    assert c2._slot.raw is not raw
    assert c2.execute("SELECT name FROM sqlite_master WHERE name='marker'").fetchone()
    c2.close()


def test_the_kill_switch_and_memory_databases_get_plain_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "ENABLED", False)
    db = MusicDatabase(str(tmp_path / "off.db"))
    c = db._get_connection()
    assert isinstance(c, sqlite3.Connection)
    c.close()
    monkeypatch.setattr(cp, "ENABLED", True)
    assert cp.acquire(":memory:", lambda same_thread: sqlite3.connect(":memory:")).__class__ is sqlite3.Connection
    assert cp.acquire("file:x?mode=memory", lambda same_thread: sqlite3.connect(":memory:")).__class__ is sqlite3.Connection


def test_the_connection_is_set_up_like_before(db):
    c = db._get_connection()
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert c.execute("SELECT norm_text('Ünï'), unidecode_lower('Ünï')").fetchone() is not None
    c.close()


def test_close_drops_the_threads_idle_connection(db):
    c = db._get_connection()
    raw = c._slot.raw
    c.close()
    db.close()
    c2 = db._get_connection()
    assert c2._slot.raw is not raw
    with pytest.raises(sqlite3.ProgrammingError):
        raw.execute("SELECT 1")                           # the old one was really closed
    c2.close()


def test_hooks_a_caller_installed_do_not_leak(db):
    c = db._get_connection()
    raw = c._slot.raw
    statements = []
    c.set_trace_callback(statements.append)
    c.set_authorizer(lambda *a: sqlite3.SQLITE_DENY)
    c.set_progress_handler(lambda: 1, 1)
    c.close()
    c2 = db._get_connection()
    assert c2._slot.raw is raw                              # cleaned and reused, not thrown away
    assert c2.execute("SELECT 1").fetchone()[0] == 1       # no authorizer, no progress abort
    assert statements == []                                 # no trace callback
    c2.close()


def test_temp_tables_do_not_outlive_the_call(db):
    c = db._get_connection()
    c.execute("CREATE TEMP TABLE staged (v TEXT PRIMARY KEY)")
    c.execute("INSERT INTO staged VALUES ('a')")
    c.close()
    c2 = db._get_connection()
    c2.execute("CREATE TEMP TABLE staged (v TEXT PRIMARY KEY)")   # would raise if it had survived
    assert c2.execute("SELECT COUNT(*) FROM staged").fetchone()[0] == 0
    c2.close()


def test_the_temp_key_helper_can_run_twice_on_one_thread(db):
    for _ in range(3):
        with db._get_connection() as conn:
            cur = conn.cursor()
            MusicDatabase._temp_key_set(cur, "tmp_keys", {"x", "y"})
            assert cur.execute("SELECT COUNT(*) FROM tmp_keys").fetchone()[0] == 2


def test_a_caller_lowered_busy_timeout_does_not_carry_over(db):
    # insert_or_update_media_track drops busy_timeout to 10s on its connection;
    # pooled, every later caller waited 10s on a locked db instead of 30s
    c = db._get_connection()
    fresh = c.execute("PRAGMA busy_timeout").fetchone()[0]
    c.close()
    c = db._get_connection()
    c.cursor().execute("PRAGMA busy_timeout = 10000")
    c.execute("PRAGMA analysis_limit = 400")
    c.close()
    c2 = db._get_connection()
    assert _hits() and c2.execute("PRAGMA busy_timeout").fetchone()[0] == fresh == 30000
    assert c2.execute("PRAGMA analysis_limit").fetchone()[0] == 0
    c2.close()


def test_idle_connections_are_capped_across_files(tmp_path, monkeypatch):
    # hundreds of temp databases in one process must not each keep an idle connection
    monkeypatch.setattr(cp, "MAX_IDLE_TOTAL", 3)
    cp._idle.clear()
    dbs = [MusicDatabase(str(tmp_path / f"{i}.db")) for i in range(6)]
    for d in dbs:
        d._get_connection().close()
    assert sum(len(b) for b in cp._idle.values()) == 3
    # the least recently returned files were the ones let go
    assert list(cp._idle) == [str(d.database_path) for d in dbs[3:]]
    for d in dbs:
        d.close()
