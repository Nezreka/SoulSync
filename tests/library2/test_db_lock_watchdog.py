"""The write-lock watchdog (core/db_lock_watchdog.py).

The production symptom this exists for: every writer in the process fails with
``database is locked`` after its full busy timeout, and the log names only the
victims. These tests pin that the probe actually detects a held write lock and
that the dump is emitted once per incident rather than once per tick.
"""

from __future__ import annotations

import sqlite3
import threading

from core import db_lock_watchdog


def _hold_write_lock(db):
    """Open a connection that owns the write lock until it is closed."""
    conn = db._get_connection()
    conn.execute("PRAGMA busy_timeout = 0")
    conn.execute("BEGIN IMMEDIATE")
    return conn


def test_probe_succeeds_on_a_free_database(legacy_db):
    assert db_lock_watchdog.probe_write_lock(legacy_db) is True


def test_probe_detects_a_held_write_lock(legacy_db):
    holder = _hold_write_lock(legacy_db)
    try:
        assert db_lock_watchdog.probe_write_lock(legacy_db, timeout_ms=50) is False
    finally:
        holder.rollback()
        holder.close()


def test_probe_writes_nothing(legacy_db):
    """It asks for the lock with BEGIN IMMEDIATE and rolls back — a pure read
    of the lock state, never a mutation of the database it is watching."""
    conn = legacy_db._get_connection()
    try:
        before = conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    finally:
        conn.close()

    assert db_lock_watchdog.probe_write_lock(legacy_db) is True

    conn = legacy_db._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == before
    finally:
        conn.close()


def test_probe_stays_quiet_when_the_database_is_unreachable(legacy_db, monkeypatch):
    """A probe failure is not evidence of a lock, and must never be reported
    as one — the watchdog exists to reduce noise, not add it."""

    def _boom():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(legacy_db, "_get_connection", _boom, raising=False)

    assert db_lock_watchdog.probe_write_lock(legacy_db) is True


def test_watchdog_dumps_once_per_incident_not_once_per_tick(legacy_db):
    clock = {"t": 0.0}
    dumps = []

    count = db_lock_watchdog.run_watchdog(
        legacy_db,
        interval_seconds=0,
        cooldown_seconds=300,
        probe=lambda: False,
        report=lambda: dumps.append(clock["t"]),
        now=lambda: clock["t"],
        max_iterations=5,
    )

    assert count == 1
    assert len(dumps) == 1


def test_watchdog_dumps_again_after_the_cooldown(legacy_db):
    clock = {"t": 0.0}
    dumps = []

    def _report():
        dumps.append(clock["t"])
        clock["t"] += 301  # the next tick is past the cooldown

    count = db_lock_watchdog.run_watchdog(
        legacy_db,
        interval_seconds=0,
        cooldown_seconds=300,
        probe=lambda: False,
        report=_report,
        now=lambda: clock["t"],
        max_iterations=3,
    )

    assert count == 3


def test_watchdog_says_nothing_while_the_database_is_healthy(legacy_db):
    dumps = []

    count = db_lock_watchdog.run_watchdog(
        legacy_db,
        interval_seconds=0,
        probe=lambda: True,
        report=lambda: dumps.append(1),
        max_iterations=10,
    )

    assert count == 0
    assert dumps == []


def test_watchdog_stops_on_its_event(legacy_db):
    stop = threading.Event()
    stop.set()

    count = db_lock_watchdog.run_watchdog(
        legacy_db, interval_seconds=0, probe=lambda: False,
        report=lambda: None, stop_event=stop,
    )

    assert count == 0


def test_stack_dump_names_threads_and_frames(legacy_db):
    dump = db_lock_watchdog.format_thread_stacks()

    assert "--- thread" in dump
    assert "test_stack_dump_names_threads_and_frames" in dump


def test_report_includes_the_stacks_and_says_what_to_look_for():
    messages = []

    db_lock_watchdog.report_blocked_writer(log=messages.append)

    assert len(messages) == 1
    assert "SQLite write lock is held" in messages[0]
    assert "--- thread" in messages[0]
