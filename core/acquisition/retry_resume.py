"""Resume interrupted acquisition retry walks after a process restart.

When a quarantine retry is mid-walk and the process restarts, the in-memory
``download_tasks`` entry is gone but its journaled state survives
(``core/acquisition/retry_state.py``). On the next acquisition worker cycle
this module rebuilds the exact legacy task — track context from the persisted
import plan, cached candidates, used and exhausted sources, retry counters —
and resubmits the EXISTING download worker. All selection, source-priority and
retry decisions stay in the shared task_worker/monitor machinery
(docs/library-v2.md §8); this module only restores state.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Tuple

from core.acquisition.candidates import redact_sensitive_text
from core.acquisition.retry_state import (
    RetryState,
    close_retry_state,
    list_active_retry_states,
    purge_expired_retry_state,
    restore_candidates,
    update_retry_progress,
)
from core.runtime_state import download_tasks, tasks_lock
from utils.logging_config import get_logger


logger = get_logger("acquisition.retry_resume")

RESUME_BATCH_LIMIT = 5


def _default_submit() -> Optional[Callable[[str], Any]]:
    """The monitor-wired worker resubmission, or None while unwired."""
    from core.downloads import monitor

    executor = monitor.missing_download_executor
    worker = monitor._download_track_worker
    if executor is None or worker is None:
        return None
    return lambda task_id: executor.submit(worker, task_id, None)


def _rebuild_task(conn: Any, state: RetryState):
    """Recreate the worker task for one journaled walk.

    Returns ``(task_dict, None)`` when the walk should resume, or
    ``(None, closure)`` where closure is ``(status, error)`` to close the
    journal row, or ``(None, None)`` to leave it for a later cycle.
    """
    from core.acquisition.grabs import get_grab
    from core.acquisition.imports import get_import
    from core.acquisition.main_pipeline_bridge import _pipeline_context

    record = get_import(conn, state.import_id)
    if record is None:
        return None, ("failed", "acquisition import row disappeared")
    if record.status == "completed":
        return None, ("completed", None)
    if record.status == "failed":
        return None, ("failed", record.error or "acquisition import failed")
    if record.status != "importing":
        # matching / needs_review own the next step; the walk stays parked.
        return None, None

    processed = {
        int(item.get("track_id") or 0)
        for item in record.result.get("processed", [])
        if isinstance(item, Mapping)
    }
    if state.track_id in processed:
        return None, ("completed", None)

    match = next(
        (
            dict(item)
            for item in record.matches
            if isinstance(item, Mapping)
            and int(item.get("track_id") or 0) == state.track_id
        ),
        None,
    )
    if match is None:
        return None, ("failed", "journaled track left the persisted import plan")

    grab = get_grab(conn, record.download_id) or {}
    context = _pipeline_context(
        conn, record, match, source=str(grab.get("source") or "staging"))

    task = {
        "id": state.task_id,
        "status": "searching",
        "track_info": dict(context["track_info"]),
        "used_sources": set(state.used_sources),
        "cached_candidates": restore_candidates(state.candidates),
        "exhausted_download_sources": set(state.exhausted_sources),
        "quarantine_retry_count": int(state.retry_count),
        "quarantine_retry_counts_by_source": dict(state.retry_counts),
        # Manual picks never journal (the requeue denies them before any
        # snapshot), so a resumed walk is always an automatic one.
        "_user_manual_pick": False,
        # Cached-first: the connection was fine, the content was wrong —
        # walk the persisted candidates before re-searching.
        "_quarantine_retry": True,
        "retry_info": "resumed after restart",
        "retry_trigger": "restart",
    }
    if state.query_count > 0:
        task["query_count"] = int(state.query_count)
    return task, None


def resume_interrupted_retry_walks(
    connection_factory: Callable[[], Any],
    *,
    limit: int = RESUME_BATCH_LIMIT,
    now: Optional[float] = None,
    submit: Optional[Callable[[str], Any]] = None,
) -> Tuple[str, ...]:
    """Resume every journaled walk whose in-memory task did not survive.

    Called from the periodic acquisition worker cycle. Also purges expired
    journal rows so terminal requests never accumulate worker data.
    """
    conn = connection_factory()
    try:
        purge_expired_retry_state(conn, now=now)
        states = list_active_retry_states(conn, now=now, limit=limit)
        conn.commit()
    finally:
        conn.close()
    if not states:
        return ()

    if submit is None:
        submit = _default_submit()
    resumed = []
    for state in states:
        with tasks_lock:
            if state.task_id in download_tasks:
                continue  # the walk is alive in this process

        conn = connection_factory()
        try:
            task, closure = _rebuild_task(conn, state)
            if task is None:
                if closure is not None:
                    close_retry_state(
                        conn,
                        status=closure[0],
                        task_id=state.task_id,
                        error=closure[1],
                    )
                    conn.commit()
                continue
            if submit is None:
                # Worker pool not wired (startup, tests) — keep the row
                # active; a later cycle resumes it.
                continue
            update_retry_progress(
                conn, state.task_id, last_progress="resumed after restart")
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - one walk must not hide others
            conn.rollback()
            logger.warning(
                "Could not resume acquisition retry %s: %s",
                state.task_id,
                redact_sensitive_text(exc),
            )
            continue
        finally:
            conn.close()

        inserted = False
        with tasks_lock:
            if state.task_id not in download_tasks:
                download_tasks[state.task_id] = task
                inserted = True
        if not inserted:
            continue
        try:
            submit(state.task_id)
        except Exception as exc:  # noqa: BLE001 - submission boundary
            with tasks_lock:
                download_tasks.pop(state.task_id, None)
            logger.warning(
                "Could not submit resumed acquisition retry %s: %s",
                state.task_id,
                redact_sensitive_text(exc),
            )
            continue
        resumed.append(state.task_id)
        logger.info(
            "Resumed interrupted acquisition retry walk %s "
            "(import %s, track %s, attempt %s)",
            state.task_id,
            state.import_id,
            state.track_id,
            state.retry_count,
        )
    return tuple(resumed)


__all__ = [
    "RESUME_BATCH_LIMIT",
    "resume_interrupted_retry_walks",
]
