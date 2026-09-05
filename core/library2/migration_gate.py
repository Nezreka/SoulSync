"""Pause the enrichment workers while the catalogue migration runs (iss32-M02).

SQLite has one writer. During Nezreka's bootstrap import every one of the
twelve enrichment workers and the automation engine kept trying to write, hit
``busy_timeout`` and logged ``database is locked``; a config save gave up after
six attempts. Neither side won — the migration was slowed by the contention it
caused, and the workers did no useful work either.

The fix is to give the migration the writer for the duration. Three things make
that safe rather than a new way to wedge the app:

- **The pause hangs on persisted state, not a flag.** The signal is
  ``lib2_bootstrap_state``: status ``running`` with a heartbeat inside
  ``STALE_AFTER_SECONDS`` (``bootstrap_is_active``). A process that dies
  mid-migration stops beating, the claim goes stale within minutes, and the
  next poll resumes the workers with nobody having to clean anything up. An
  in-memory flag would have survived exactly as long as the process that
  crashed.
- **Only what this supervisor paused gets resumed.** A worker the user paused
  by hand, or one that is not running at all, is left exactly as it was.
- **It never blocks.** Polling is a single indexed read of one row; the
  supervisor thread is a daemon and every call into a worker is guarded.

This is deliberately a supervisor and not a check inside each worker loop:
sixteen workers would have meant sixteen edits and a seventeenth worker
silently missing out. They already expose ``pause()``/``resume()``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.migration_gate")

POLL_INTERVAL_SECONDS = 5.0
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def request_can_mutate(method: Any) -> bool:
    """Whether an HTTP method must be held behind the migration barrier."""
    return str(method or "").upper() not in SAFE_HTTP_METHODS


def endpoint_is_blocked(endpoint: Any, blocked: Iterable[str]) -> bool:
    """Whether a Flask endpoint name is on the migration barrier's list.

    MIG-01: the list is written and maintained as *handler function names*,
    but Flask reports a blueprint route as the qualified ``blueprint.function``.
    Upstream keeps lifting route functions out of ``web_server.py`` into
    blueprints, and every such move silently turned an entry into a dead
    string — 77 of 105 by the time this was measured, including the backup
    restore that can swap the database out from under a running import.

    Matching the unqualified tail as well ties the barrier to the handler
    rather than to wherever it happens to be registered today. The blast
    radius of a tail collision is a mutation refused during a migration, which
    is the direction this gate is supposed to fail in.
    """
    name = str(endpoint or "")
    if not name:
        return False
    blocked = blocked if isinstance(blocked, (set, frozenset)) else frozenset(blocked)
    return name in blocked or name.rpartition(".")[2] in blocked

# In-process heavy work that is NOT a claimed bootstrap run: the deferred
# convergence passes (iss32-M03). They deserve the same quiet as the import —
# on an interrupted migration they are the same amount of writing — but they
# take no claim, so the persisted signal cannot see them.
#
# An in-memory counter is the right shape here and a wrong one for the import:
# this work only ever exists inside the process holding the counter, so it
# cannot outlive its owner the way a claim row can.
_local_lock = threading.Lock()
_active_sections = 0
_deferred_workers: list = []
_deferred_callbacks: list = []
_deferred_lock = threading.Lock()


@contextmanager
def migration_activity() -> Iterator[None]:
    """Mark heavy in-process catalogue work for the duration of the block."""
    global _active_sections
    with _local_lock:
        _active_sections += 1
    try:
        yield
    finally:
        with _local_lock:
            _active_sections -= 1


def local_migration_active() -> bool:
    with _local_lock:
        return _active_sections > 0


def migration_required(database: Any) -> bool:
    """True from upgrade detection through the final committed import step."""
    try:
        from core.library2.bootstrap import (
            bootstrap_is_active, get_state, source_row_count, source_watermark,
        )
        if local_migration_active() or bootstrap_is_active(database):
            return True
        state = get_state(database)
        # Legacy is immutable after cutover. Once imported, runtime never polls it.
        if state.get("status") == "done":
            return False
        return source_row_count(source_watermark(database)) > 0
    except Exception as exc:  # half-known upgrade state must never admit writers
        logger.warning("Could not verify Library v2 upgrade state: %s", exc)
        return True


def defer_or_start(worker: Any, database: Any) -> bool:
    """Start a worker now, or hold it unstarted until migration completes."""
    if not migration_required(database):
        worker.start()
        return True
    with _deferred_lock:
        if worker not in _deferred_workers:
            _deferred_workers.append(worker)
    logger.info("Library v2 upgrade pending — deferred %s", _worker_name(worker))
    return False


def defer_or_call(callback: Callable[[], Any], database: Any, label: str) -> bool:
    """Run a background-service starter now, or after the upgrade barrier."""
    if not migration_required(database):
        callback()
        return True
    with _deferred_lock:
        _deferred_callbacks.append((label, callback))
    logger.info("Library v2 upgrade pending — deferred %s", label)
    return False


def start_deferred_workers(database: Any) -> int:
    if migration_required(database):
        return 0
    with _deferred_lock:
        workers, _deferred_workers[:] = list(_deferred_workers), []
        callbacks, _deferred_callbacks[:] = list(_deferred_callbacks), []
    started = 0
    for worker in workers:
        try:
            worker.start()
            started += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not start deferred %s: %s", _worker_name(worker), exc)
    for label, callback in callbacks:
        try:
            callback()
            started += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not start deferred %s: %s", label, exc)
    if started:
        logger.info("Library v2 upgrade complete — started %d deferred worker(s)", started)
    return started


def _worker_name(worker: Any) -> str:
    return type(worker).__name__


class MigrationPauseSupervisor:
    """Hold the enrichment workers still for as long as a migration is alive.

    ``workers`` is a callable so the supervisor can start before the workers
    exist and pick up whatever is registered at poll time — worker
    construction in ``web_server`` is spread across startup and some workers
    are conditional on configuration.
    """

    def __init__(self, database: Any,
                 workers: Callable[[], Iterable[Any]],
                 automation_engine: Any = None,
                 *, interval: float = POLL_INTERVAL_SECONDS,
                 is_active: Optional[Callable[[Any], bool]] = None) -> None:
        self._database = database
        self._workers = workers
        self._engine = automation_engine
        self._interval = max(float(interval), 0.5)
        self._is_active = is_active
        self._paused_by_us: list = []
        self._engine_paused = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="Lib2MigrationPauseSupervisor", daemon=True)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> "MigrationPauseSupervisor":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        # Leaving workers paused because the app is shutting down would be
        # harmless now and confusing after the next start, when a stale
        # in-memory pause has no owner. Undo ours on the way out.
        self._resume_ours()

    # --- polling -----------------------------------------------------------

    def _active(self) -> bool:
        if self._is_active is not None:
            return bool(self._is_active(self._database))
        return migration_required(self._database)

    def tick(self) -> bool:
        """One poll. Returns whether a migration is currently active.

        Public so a test can drive the state machine without a thread and a
        clock.
        """
        try:
            active = self._active()
        except Exception as exc:  # noqa: BLE001 - never kill the supervisor
            logger.debug("migration gate poll failed: %s", exc)
            self._pause_all()
            return True
        if active:
            self._pause_all()
        else:
            self._resume_ours()
        return active

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.tick()

    # --- pause / resume ----------------------------------------------------

    def _pause_all(self) -> None:
        try:
            workers = list(self._workers() or ())
        except Exception as exc:  # noqa: BLE001
            logger.debug("migration gate could not enumerate workers: %s", exc)
            workers = []
        for worker in workers:
            if worker is None or worker in self._paused_by_us:
                continue
            # Not running, or already paused by someone else: not ours to
            # touch, and above all not ours to resume later.
            if not getattr(worker, "running", False):
                continue
            if getattr(worker, "paused", False):
                continue
            try:
                worker.pause()
            except Exception as exc:  # noqa: BLE001
                logger.debug("could not pause %s: %s", _worker_name(worker), exc)
                continue
            self._paused_by_us.append(worker)
        if self._paused_by_us and not self._engine_paused:
            logger.info("Library v2 migration active — paused %d enrichment worker(s)",
                        len(self._paused_by_us))
        if self._engine is not None and not self._engine_paused:
            try:
                self._engine.pause_for_migration()
                self._engine_paused = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("could not pause the automation engine: %s", exc)

    def _resume_ours(self) -> None:
        if self._is_active is None:
            start_deferred_workers(self._database)
        if not self._paused_by_us and not self._engine_paused:
            return
        resumed = 0
        for worker in self._paused_by_us:
            try:
                worker.resume()
                resumed += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("could not resume %s: %s", _worker_name(worker), exc)
        self._paused_by_us = []
        if self._engine is not None and self._engine_paused:
            try:
                self._engine.resume_after_migration()
            except Exception as exc:  # noqa: BLE001
                logger.debug("could not resume the automation engine: %s", exc)
            self._engine_paused = False
        if resumed:
            logger.info("Library v2 migration finished — resumed %d enrichment worker(s)",
                        resumed)


__all__ = [
    "POLL_INTERVAL_SECONDS",
    "MigrationPauseSupervisor",
    "defer_or_call",
    "defer_or_start",
    "local_migration_active",
    "migration_required",
    "migration_activity",
    "start_deferred_workers",
]
