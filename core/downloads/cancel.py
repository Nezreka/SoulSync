"""Download cancellation + clear helpers.

Four discrete operations lifted from web_server.py:

- `cancel_single_download(client, run_async, download_id, username)` — cancel
  one slskd transfer.
- `cancel_all_active(client, run_async, sweep_callback)` — cancel every
  active slskd transfer, then clear the now-cancelled ones, then sweep
  empty download directories.
- `clear_finished_active(client, run_async, sweep_callback)` — clear all
  terminal transfers from slskd (no cancel step), sweep dirs.
- `clear_completed_local(statuses)` — prune terminal-status tasks from the
  local `download_tasks` tracker, drop empty batches, drop their locks.
  Pure local mutation, doesn't touch slskd.
- `remove_local_task(task_id)` — take one failed / not-found / cancelled
  task off the local tracker (the Downloads page's Remove from list).

The slskd-touching helpers take the soulseek client and run_async callback
explicitly; the local helper imports its globals directly from
`core.runtime_state` since those are module-level shared state and every
caller sees the same dict.

Out of scope for this PR (deferred to the batch-lifecycle lift):
- `cancel_download_task` (calls _on_download_completed)
- `cancel_task_v2` + `_atomic_cancel_task` (manipulate batch active_count)
"""

from __future__ import annotations

from utils.logging_config import get_logger
from typing import Callable

from core.runtime_state import (
    batch_locks,
    download_batches,
    download_tasks,
    tasks_lock,
)

logger = get_logger("downloads.cancel")

# The Downloads page's two terminal groups. Clear finished takes the first,
# Clear failed and Remove from list the second, Clear everything both.
FINISHED_STATUSES = frozenset({'completed', 'skipped', 'already_owned'})
FAILED_STATUSES = frozenset({'failed', 'not_found', 'cancelled'})
_TERMINAL_STATUSES = FINISHED_STATUSES | FAILED_STATUSES


def cancel_single_download(download_orchestrator, run_async: Callable,
                            download_id: str, username: str) -> bool:
    """Cancel one specific slskd download (with `remove=True`)."""
    logger.info(
        f"[CancelTrigger:api.manual_cancel_single] download_id={download_id} username={username}"
    )
    return run_async(download_orchestrator.cancel_download(download_id, username, remove=True))


def cancel_all_active(download_orchestrator, run_async: Callable,
                       sweep_callback: Callable[[], None]) -> tuple[bool, str]:
    """Cancel every active slskd download, clear the resulting ones, sweep dirs.

    Returns `(success, message)` so the route can map to the right HTTP shape.
    """
    cancel_success = run_async(download_orchestrator.cancel_all_downloads())
    if not cancel_success:
        return False, "Failed to cancel active downloads."

    run_async(download_orchestrator.clear_all_completed_downloads())
    sweep_callback()
    return True, "All downloads cancelled and cleared."


def clear_finished_active(download_orchestrator, run_async: Callable,
                           sweep_callback: Callable[[], None]) -> bool:
    """Clear all terminal transfers from slskd, sweep dirs on success."""
    success = run_async(download_orchestrator.clear_all_completed_downloads())
    if success:
        sweep_callback()
    return success


def _batch_is_running(batch) -> bool:
    """True while any task in the batch's queue is not terminal yet."""
    queue = batch.get('queue', []) if isinstance(batch, dict) else []
    return any(
        download_tasks.get(tid, {}).get('status') not in _TERMINAL_STATUSES
        for tid in queue if tid in download_tasks
    )


def _drop_empty_batches() -> None:
    """Trim each batch's queue to tasks still tracked; drop the empty ones."""
    empty_batches = []
    for bid, batch in download_batches.items():
        remaining = [t for t in batch.get('queue', []) if t in download_tasks]
        if not remaining:
            empty_batches.append(bid)
        else:
            batch['queue'] = remaining
    for bid in empty_batches:
        del download_batches[bid]
        if bid in batch_locks:
            del batch_locks[bid]


def clear_completed_local(statuses=None) -> int:
    """Remove terminal tasks from the local tracker.

    ``statuses`` picks which terminal tasks go (``FINISHED_STATUSES``,
    ``FAILED_STATUSES``); the default is every terminal status, which is what
    Clear everything does. Also prunes batches whose queues are now empty, and
    removes the matching `batch_locks` entry. Returns the number of cleared
    tasks.
    """
    wanted = _TERMINAL_STATUSES if statuses is None else (frozenset(statuses) & _TERMINAL_STATUSES)
    cleared = 0
    with tasks_lock:
        # Protect tasks belonging to a still-active batch. A batch is "active"
        # while any of its queued tasks is non-terminal (still searching /
        # downloading / queued / post-processing). Pruning a batch's completed
        # or failed tasks mid-run would yank them out of the Downloads page —
        # and failed/cancelled rows aren't recoverable from library_history —
        # so the user would never see them until the batch ended. Keep the whole
        # active batch intact; it gets cleaned by a later run once it finishes.
        protected_task_ids: set = set()
        for batch in download_batches.values():
            if _batch_is_running(batch):
                protected_task_ids.update(batch.get('queue', []))

        task_ids_to_remove = [
            tid for tid, task in download_tasks.items()
            if task.get('status') in wanted and tid not in protected_task_ids
        ]
        for tid in task_ids_to_remove:
            del download_tasks[tid]
            cleared += 1

        _drop_empty_batches()

    return cleared


def remove_local_task(task_id: str) -> str:
    """Take one failed, not-found or cancelled task off the local tracker.

    Nothing is retried and nothing on disk or in the history is touched; the
    row simply leaves the Downloads page. Returns 'removed', or why not:
    'missing' (no such task), 'not_removable' (any other status) or
    'batch_running' (its batch still has work in flight -- the same rule
    clear_completed_local keeps, so a live batch's rows stay put until it ends).
    """
    with tasks_lock:
        task = download_tasks.get(task_id)
        if task is None:
            return 'missing'
        if task.get('status') not in FAILED_STATUSES:
            return 'not_removable'
        for batch in download_batches.values():
            if task_id in (batch.get('queue', []) if isinstance(batch, dict) else []) \
                    and _batch_is_running(batch):
                return 'batch_running'
        del download_tasks[task_id]
        _drop_empty_batches()
    return 'removed'
