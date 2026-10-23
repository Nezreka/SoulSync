"""Name the thread that is holding the SQLite write lock.

WAL gives SQLite exactly one writer. When something takes that write lock and
then does slow work — a provider call, a filesystem walk, an unfinished
transaction in a thread that went away — every other writer in the process
waits out its full ``busy_timeout`` and fails with ``database is locked``. The
log then fills with *victims*: notifications, automations, UI preferences,
repair jobs, the acquisition sweep. None of them names the holder, which is why
this failure mode can be investigated repeatedly without an answer.

This watchdog closes that gap. It periodically asks SQLite for the write lock
with a short timeout and writes nothing:

- got it → the database is healthy, say nothing;
- refused → someone is holding it. Dump every thread's stack.

The holder is in that dump, parked on whatever line it is blocked on. One
occurrence is then enough to identify it, instead of another round of guessing
from the victim list.

Deliberately quiet: a dump is rate-limited, and a healthy installation never
logs anything from here at all.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("db_lock_watchdog")

# Long enough that a normal short write (a status update, a heartbeat) is never
# mistaken for a stuck holder, short enough that the probe itself never becomes
# one of the waiters it is trying to explain.
PROBE_TIMEOUT_MS = 2000
PROBE_INTERVAL_SECONDS = 15
# One dump per five minutes. A wedged database stays wedged for minutes, and
# the first dump already contains the answer; repeating it every 15s would bury
# the log it is meant to make readable.
DUMP_COOLDOWN_SECONDS = 300


def probe_write_lock(database: Any, *, timeout_ms: int = PROBE_TIMEOUT_MS) -> bool:
    """Return True iff the write lock is obtainable right now.

    ``BEGIN IMMEDIATE`` asks for the writer lock without writing anything, so
    this is a pure observation: it commits nothing and rolls back at once.
    """
    try:
        conn = database._get_connection()
    except Exception as exc:  # noqa: BLE001 - a probe never breaks the app
        logger.debug("write-lock probe could not connect: %s", exc)
        return True
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return False
        logger.debug("write-lock probe failed: %s", exc)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("write-lock probe failed: %s", exc)
        return True
    finally:
        try:
            conn.close()
        except Exception as close_exc:  # noqa: BLE001
            logger.debug("write-lock probe close failed: %s", close_exc)


def format_thread_stacks() -> str:
    """Every live thread with its stack, newest frame last."""
    names: Dict[int, str] = {t.ident: t.name for t in threading.enumerate() if t.ident}
    blocks = []
    for thread_id, frame in sorted(sys._current_frames().items()):
        name = names.get(thread_id, "unknown")
        stack = "".join(traceback.format_stack(frame))
        blocks.append(f"--- thread {name} ({thread_id}) ---\n{stack}")
    return "\n".join(blocks)


def report_blocked_writer(*, log: Optional[Callable[..., None]] = None) -> None:
    """Log why writes are failing: the stack of every thread in the process."""
    emit = log or (lambda message: logger.error("%s", message))
    emit(
        f"SQLite write lock is held longer than {PROBE_TIMEOUT_MS}ms, so every "
        "other writer in this process is queued behind it. Dumping all thread "
        "stacks — the holder is the thread parked inside a database call, or "
        "inside a network/filesystem call it made after writing:\n"
        + format_thread_stacks()
    )


def run_watchdog(
    database: Any,
    *,
    interval_seconds: float = PROBE_INTERVAL_SECONDS,
    cooldown_seconds: float = DUMP_COOLDOWN_SECONDS,
    stop_event: Optional[threading.Event] = None,
    probe: Optional[Callable[[], bool]] = None,
    report: Optional[Callable[[], None]] = None,
    max_iterations: Optional[int] = None,
    now: Callable[[], float] = time.monotonic,
) -> int:
    """Watch the write lock forever. Returns how many dumps it emitted.

    ``max_iterations`` bounds the loop for tests; production passes nothing and
    the thread runs for the life of the process.
    """
    stop_event = stop_event or threading.Event()
    do_probe = probe or (lambda: probe_write_lock(database))
    do_report = report or (lambda: report_blocked_writer())

    dumps = 0
    last_dump: Optional[float] = None
    iterations = 0
    while not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1
        try:
            if not do_probe():
                moment = now()
                if last_dump is None or (moment - last_dump) >= cooldown_seconds:
                    last_dump = moment
                    dumps += 1
                    do_report()
        except Exception as exc:  # noqa: BLE001 - diagnostics never break the app
            logger.debug("write-lock watchdog tick skipped: %s", exc)
        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event.wait(interval_seconds):
            break
    return dumps


def start_watchdog(database: Any, **kwargs: Any) -> threading.Thread:
    thread = threading.Thread(
        target=run_watchdog, args=(database,), kwargs=kwargs,
        name="DbLockWatchdog", daemon=True,
    )
    thread.start()
    return thread


__all__ = [
    "DUMP_COOLDOWN_SECONDS",
    "PROBE_INTERVAL_SECONDS",
    "PROBE_TIMEOUT_MS",
    "format_thread_stacks",
    "probe_write_lock",
    "report_blocked_writer",
    "run_watchdog",
    "start_watchdog",
]
