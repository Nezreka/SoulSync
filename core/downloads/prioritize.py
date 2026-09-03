"""Download queue prioritisation helpers.

The active-downloads UI lets a user ask for a queued track or batch to run
next. This module keeps that as scheduler metadata only: no active worker is
cancelled, and no concurrency limit is bypassed.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from core.runtime_state import download_batches, download_tasks, tasks_lock

PENDING_TASK_STATUSES = {"pending", "queued"}
TERMINAL_BATCH_PHASES = {"complete", "error", "cancelled", "failed"}
PROMOTED_AT_KEY = "_download_next_requested_at"


def _as_queue_index(batch: Dict[str, Any], queue: list) -> int:
    try:
        raw = int(batch.get("queue_index") or 0)
    except (TypeError, ValueError):
        raw = 0
    return min(max(raw, 0), len(queue))


def batch_has_unstarted_work(batch: Dict[str, Any], tasks: Dict[str, Dict[str, Any]]) -> bool:
    """Return whether a batch has task rows that a worker can still claim."""
    if not isinstance(batch, dict):
        return False
    if batch.get("phase") in TERMINAL_BATCH_PHASES:
        return False
    queue = batch.get("queue")
    if not isinstance(queue, list):
        return False
    queue_index = _as_queue_index(batch, queue)
    for task_id in queue[queue_index:]:
        task = tasks.get(task_id)
        if task and task.get("status") in PENDING_TASK_STATUSES:
            return True
    return False


def promoted_at(batch: Dict[str, Any]) -> float:
    try:
        return float(batch.get(PROMOTED_AT_KEY) or 0)
    except (TypeError, ValueError):
        return 0.0


def top_promoted_batch_id(
    batches: Dict[str, Dict[str, Any]],
    tasks: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """Return the newest promoted batch that has startable work."""
    best_id = None
    best_at = 0.0
    for batch_id, batch in batches.items():
        requested_at = promoted_at(batch)
        if requested_at <= 0:
            continue
        if not batch_has_unstarted_work(batch, tasks):
            continue
        if requested_at > best_at:
            best_id = batch_id
            best_at = requested_at
    return best_id


def should_defer_batch_start(
    batch_id: str,
    batches: Dict[str, Dict[str, Any]],
    tasks: Dict[str, Dict[str, Any]],
) -> bool:
    """Whether this batch should yield to a user-promoted batch.

    A promoted batch waits until already-active workers in other batches drain.
    Non-promoted batches do not refill while a promoted batch has unstarted
    work. Together that gives "finish the current wave, then switch" semantics.
    """
    top_id = top_promoted_batch_id(batches, tasks)
    if not top_id:
        return False
    if batch_id != top_id:
        return True
    return any(
        other_id != batch_id and int((batch or {}).get("active_count") or 0) > 0
        for other_id, batch in batches.items()
    )


def batch_wake_sort_key(batch_id: str, batch: Dict[str, Any]) -> Tuple[int, float]:
    """Sort promoted batches first, newest promotion first.

    Python's sort is stable, so equal keys preserve the existing runtime order.
    """
    requested_at = promoted_at(batch)
    if requested_at > 0:
        return (0, -requested_at)
    return (1, 0.0)


def prioritize_batch_next(batch_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Mark a batch as the next batch to run after current active work drains."""
    with tasks_lock:
        batch = download_batches.get(batch_id)
        if not batch:
            return False, "Batch not found", None
        if batch.get("phase") in TERMINAL_BATCH_PHASES:
            return False, "Batch is already finished", None

        requested_at = time.time()
        batch[PROMOTED_AT_KEY] = requested_at
        return True, "Batch will download next.", {
            "batch_id": batch_id,
            "download_next_requested_at": requested_at,
        }


def prioritize_task_next(
    task_id: str,
    get_batch_lock: Callable[[str], Any],
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Move a pending task to the front of its batch's unstarted queue slice."""
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False, "Task not found", None
        batch_id = task.get("batch_id")
        if not batch_id:
            return False, "Task is not part of a batch", None

    batch_lock = get_batch_lock(batch_id)
    with batch_lock:
        with tasks_lock:
            task = download_tasks.get(task_id)
            batch = download_batches.get(batch_id)
            if not task:
                return False, "Task not found", None
            if not batch:
                return False, "Batch not found", None
            if task.get("status") not in PENDING_TASK_STATUSES:
                return False, "Only queued tracks can be moved next", None

            queue = batch.get("queue")
            if not isinstance(queue, list):
                return False, "Batch queue is not available", None
            try:
                current_pos = queue.index(task_id)
            except ValueError:
                return False, "Task is not in its batch queue", None

            queue_index = _as_queue_index(batch, queue)
            if current_pos < queue_index:
                return False, "Track has already started", None
            if current_pos != queue_index:
                queue.pop(current_pos)
                queue.insert(queue_index, task_id)

            requested_at = time.time()
            task["download_next_requested_at"] = requested_at
            batch[PROMOTED_AT_KEY] = requested_at
            return True, "Track will download next.", {
                "task_id": task_id,
                "batch_id": batch_id,
                "batch_position": queue_index + 1,
                "download_next_requested_at": requested_at,
            }
