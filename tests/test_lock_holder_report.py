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


def test_a_busy_server_names_its_busiest_threads():
    import threading as _th
    me = _th.current_thread()
    before = {me.native_id: 1.0, 999999: 5.0}
    after = {me.native_id: 13.0, 999999: 5.5}
    text = db_lock_watchdog.format_busy_threads(before, after, 15.0)
    assert text is not None
    first, second = text.splitlines()[1:3]
    assert me.name in first and "80% of a core" in first and " at " in first
    assert "thread 999999: 3% of a core" in second


def test_a_quiet_server_logs_nothing():
    assert db_lock_watchdog.format_busy_threads({1: 1.0}, {1: 2.0}, 15.0) is None


def test_the_watchdog_reports_busy_threads_once_per_window():
    snaps = iter([{1: 0.0}, {1: 30.0}, {1: 60.0}, {1: 90.0}])
    reports = []
    db_lock_watchdog.run_watchdog(
        None, probe=lambda: True, report=lambda: None, loop_probe=lambda: True,
        cpu_snapshot=lambda: next(snaps), cpu_report=reports.append,
        interval_seconds=0, cooldown_seconds=300, max_iterations=3,
        now=iter([0.0, 15.0, 30.0, 45.0]).__next__)
    assert len(reports) == 1 and "200% of a core" in reports[0]


def test_a_sliver_of_a_window_is_never_reported():
    # the first tick after start: 0.2s of start-up cpu read as 2000% of a core
    assert db_lock_watchdog.format_busy_threads({1: 0.0}, {1: 5.0}, 0.2) is None
