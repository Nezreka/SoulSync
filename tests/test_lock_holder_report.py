"""The lock watchdog names the thread holding an open write transaction.

Its dump used to list every thread's stack and leave the reader to guess which
one held the lock; the Oct 31 2026 storm went unexplained that way twice."""

from __future__ import annotations

import sqlite3
import threading

from core import db_lock_watchdog
from database import connection_pool


def _opener(path):
    return lambda same_thread: sqlite3.connect(path, check_same_thread=same_thread)


def test_an_open_write_names_its_thread(tmp_path):
    path = str(tmp_path / "lib.db")
    setup = sqlite3.connect(path)
    setup.execute("CREATE TABLE t (x)")
    setup.commit()
    setup.close()

    held = threading.Event()
    done = threading.Event()

    def writer():
        conn = connection_pool.acquire(path, _opener(path))
        conn.execute("INSERT INTO t VALUES (1)")  # opens the transaction
        held.set()
        done.wait(5)
        conn.rollback()
        conn.close()

    th = threading.Thread(target=writer, name="quality-backfill-writer")
    th.start()
    try:
        assert held.wait(5)
        holders = connection_pool.open_transactions()
        assert [h["thread"] for h in holders] == ["quality-backfill-writer"]
        assert holders[0]["path"] == "lib.db"
        report = db_lock_watchdog.format_open_transactions()
        assert "quality-backfill-writer" in report and "holder is among them" in report
    finally:
        done.set()
        th.join(5)
    assert connection_pool.open_transactions() == []


def test_a_read_only_connection_is_not_a_holder(tmp_path):
    path = str(tmp_path / "lib.db")
    conn = connection_pool.acquire(path, _opener(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
    conn.commit()
    conn.execute("SELECT * FROM t").fetchall()
    assert connection_pool.open_transactions() == []
    conn.close()
    assert "none on pooled connections" in db_lock_watchdog.format_open_transactions()


def test_a_stalled_shared_loop_is_reported_with_every_stack(monkeypatch):
    import asyncio
    import time as _time

    from utils.async_helpers import run_async

    # a coroutine doing blocking work freezes the shared loop
    async def blocker():
        _time.sleep(1.5)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(run_async, blocker())
        _time.sleep(0.2)
        assert db_lock_watchdog.probe_shared_loop(timeout=0.5) is False
    assert db_lock_watchdog.probe_shared_loop(timeout=2) is True
    seen = []
    db_lock_watchdog.report_blocked_loop(log=seen.append)
    assert "shared async loop" in seen[0] and "--- thread" in seen[0]
    assert asyncio  # imported for the coroutine above


def test_the_watchdog_dumps_once_per_cooldown_for_a_stalled_loop():
    reports = []
    db_lock_watchdog.run_watchdog(
        None, probe=lambda: True, report=lambda: None,
        loop_probe=lambda: False, loop_report=lambda: reports.append(1),
        interval_seconds=0, cooldown_seconds=300, max_iterations=3,
        now=lambda: 1000.0)
    assert reports == [1]
