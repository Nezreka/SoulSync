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


LOOP_PROBE_TIMEOUT_SECONDS = 2.0


def probe_shared_loop(*, timeout: float = LOOP_PROBE_TIMEOUT_SECONDS) -> bool:
    """True iff the shared async loop runs a no-op within ``timeout``.

    Most of the app reaches clients through that one loop (utils.async_helpers);
    a coroutine that blocks it (a thread lock, a sync call) stalls every page
    that needs it. On Oct 31 2026 the Downloads page took two minutes with
    nothing in the log to say why."""
    try:
        import asyncio
        from utils.async_helpers import AsyncCallTimeout, run_async
    except Exception:  # noqa: BLE001 - no loop to watch
        return True
    try:
        run_async(asyncio.sleep(0), timeout=timeout)
        return True
    except AsyncCallTimeout:
        return False
    except Exception as exc:  # noqa: BLE001 - a probe never breaks the app
        logger.debug("shared loop probe failed: %s", exc)
        return True


def report_blocked_loop(*, log: Optional[Callable[..., None]] = None) -> None:
    emit = log or (lambda message: logger.error("%s", message))
    emit(
        f"The shared async loop did not run a no-op within {LOOP_PROBE_TIMEOUT_SECONDS:g}s, "
        "so every page and worker waiting on it is stalled. The blocker is the "
        "SoulSyncAsyncLoop thread's stack below (a coroutine doing blocking work):\n"
        + format_thread_stacks()
    )


# a busy server: more than this share of one core, averaged over a tick
BUSY_CPU_FRACTION = 0.7
BUSY_TOP_THREADS = 8


def thread_cpu_snapshot() -> Optional[Dict[int, float]]:
    """{native thread id: cpu seconds so far} for this process, or None
    without psutil."""
    try:
        import psutil
        return {t.id: t.user_time + t.system_time for t in psutil.Process().threads()}
    except Exception:  # noqa: BLE001 - diagnostics only
        return None


def format_busy_threads(before: Dict[int, float], after: Dict[int, float],
                        elapsed: float) -> Optional[str]:
    """The threads that used the CPU between two snapshots, busiest first,
    each with its share of a core and the line it is on now; None when the
    process as a whole stayed under BUSY_CPU_FRACTION.

    Pages that take milliseconds on their own took seconds live (Oct 31 2026):
    one process runs every request and dozens of background threads, and they
    share the GIL. This says which threads are taking it."""
    if elapsed <= 0:
        return None
    used = {tid: after[tid] - before.get(tid, 0.0) for tid in after}
    total = sum(v for v in used.values() if v > 0)
    if total / elapsed < BUSY_CPU_FRACTION:
        return None
    names = {th.native_id: th.name for th in threading.enumerate() if th.native_id}
    idents = {th.native_id: th.ident for th in threading.enumerate() if th.native_id}
    frames = sys._current_frames()
    lines = []
    for tid, cpu in sorted(used.items(), key=lambda kv: -kv[1])[:BUSY_TOP_THREADS]:
        if cpu <= 0:
            break
        where = ""
        frame = frames.get(idents.get(tid))
        if frame is not None:
            last = traceback.extract_stack(frame)[-1]
            where = f" at {last.filename.rsplit('SoulSync', 1)[-1]}:{last.lineno} {last.name}"
        lines.append(f"  {names.get(tid, f'thread {tid}')}: {cpu / elapsed * 100:.0f}% of a core{where}")
    return (f"The server used {total / elapsed * 100:.0f}% of a core over the last "
            f"{elapsed:.0f}s; busiest threads:\n" + "\n".join(lines))


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
        "other writer in this process is queued behind it.\n"
        + format_open_transactions()
        + "Dumping all thread stacks — the holder is the thread parked inside a "
        "database call, or inside a network/filesystem call it made after writing:\n"
        + format_thread_stacks()
    )


def format_open_transactions() -> str:
    """The pooled connections holding a transaction open right now, oldest
    first. The oldest one is almost always the holder; its thread's stack
    below says what it is doing while it holds it."""
    try:
        from database.connection_pool import open_transactions
        holders = open_transactions()
    except Exception as exc:  # noqa: BLE001 - diagnostics never break the app
        return f"Open transactions: unavailable ({exc})\n"
    if not holders:
        return ("Open transactions: none on pooled connections (the holder opened "
                "its own connection, or another process has the file)\n")
    lines = [f"  {h['thread']}: open {h['age_s']}s on {h['path']}" for h in holders]
    return ("Open transactions, oldest first (the holder is among them; a writer still "
            "waiting for the lock shows here too, its stack below is parked in execute):\n"
            + "\n".join(lines) + "\n")


def run_watchdog(
    database: Any,
    *,
    interval_seconds: float = PROBE_INTERVAL_SECONDS,
    cooldown_seconds: float = DUMP_COOLDOWN_SECONDS,
    stop_event: Optional[threading.Event] = None,
    probe: Optional[Callable[[], bool]] = None,
    report: Optional[Callable[[], None]] = None,
    loop_probe: Optional[Callable[[], bool]] = None,
    loop_report: Optional[Callable[[], None]] = None,
    cpu_snapshot: Optional[Callable[[], Optional[Dict[int, float]]]] = None,
    cpu_report: Optional[Callable[[str], None]] = None,
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

    do_loop_probe = loop_probe or (lambda: True)
    do_loop_report = loop_report or (lambda: report_blocked_loop())

    do_cpu_report = cpu_report or (lambda text: logger.warning("%s", text))

    dumps = 0
    last_dump: Optional[float] = None
    last_loop_dump: Optional[float] = None
    last_cpu_report: Optional[float] = None
    cpu_before = cpu_snapshot() if cpu_snapshot else None
    cpu_at = now()
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
            if not do_loop_probe():
                moment = now()
                if last_loop_dump is None or (moment - last_loop_dump) >= cooldown_seconds:
                    last_loop_dump = moment
                    dumps += 1
                    do_loop_report()
            if cpu_snapshot is not None:
                moment = now()
                cpu_after = cpu_snapshot()
                if cpu_before is not None and cpu_after is not None:
                    text = format_busy_threads(cpu_before, cpu_after, moment - cpu_at)
                    if text and (last_cpu_report is None
                                 or (moment - last_cpu_report) >= 2 * cooldown_seconds):
                        last_cpu_report = moment
                        do_cpu_report(text)
                cpu_before, cpu_at = cpu_after, moment
        except Exception as exc:  # noqa: BLE001 - diagnostics never break the app
            logger.debug("write-lock watchdog tick skipped: %s", exc)
        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event.wait(interval_seconds):
            break
    return dumps


def start_watchdog(database: Any, **kwargs: Any) -> threading.Thread:
    # the live watchdog also watches the shared async loop; tests drive
    # run_watchdog directly and pass their own probes
    kwargs.setdefault("loop_probe", probe_shared_loop)
    kwargs.setdefault("cpu_snapshot", thread_cpu_snapshot)
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
    "format_busy_threads",
    "format_open_transactions",
    "format_thread_stacks",
    "probe_shared_loop",
    "probe_write_lock",
    "report_blocked_loop",
    "report_blocked_writer",
    "run_watchdog",
    "start_watchdog",
]
