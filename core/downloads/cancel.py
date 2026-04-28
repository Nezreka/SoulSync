"""Download cancellation + clear helpers.

Four discrete operations lifted from web_server.py:

- `cancel_single_download(client, run_async, download_id, username)` — cancel
  one slskd transfer.
- `cancel_all_active(client, run_async, sweep_callback)` — cancel every
  active slskd transfer, then clear the now-cancelled ones, then sweep
  empty download directories.
- `clear_finished_active(client, run_async, sweep_callback)` — clear all
  terminal transfers from slskd (no cancel step), sweep dirs.
- `clear_completed_local()` — prune terminal-status tasks from the
  local `download_tasks` tracker, drop empty batches, drop their locks.
  Pure local mutation, doesn't touch slskd.

The slskd-touching helpers take the soulseek client and run_async callback
explicitly; the local helper imports its globals directly from
`core.runtime_state` since those are module-level shared state and every
caller sees the same dict.

Out of scope for this PR (deferred to the batch-lifecycle lift):
- `cancel_download_task` (calls _on_download_completed)
- `cancel_task_v2` + `_atomic_cancel_task` (manipulate batch active_count)
"""

from __future__ import annotations

import logging
from typing import Callable

from core.runtime_state import (
    batch_locks,
    download_batches,
    download_tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    'completed', 'failed', 'not_found', 'cancelled', 'skipped', 'already_owned',
}


def cancel_single_download(soulseek_client, run_async: Callable,
                            download_id: str, username: str) -> bool:
    """Cancel one specific slskd download (with `remove=True`)."""
    return run_async(soulseek_client.cancel_download(download_id, username, remove=True))


def cancel_all_active(soulseek_client, run_async: Callable,
                       sweep_callback: Callable[[], None]) -> tuple[bool, str]:
    """Cancel every active slskd download, clear the resulting ones, sweep dirs.

    Returns `(success, message)` so the route can map to the right HTTP shape.
    """
    cancel_success = run_async(soulseek_client.cancel_all_downloads())
    if not cancel_success:
        return False, "Failed to cancel active downloads."

    run_async(soulseek_client.clear_all_completed_downloads())
    sweep_callback()
    return True, "All downloads cancelled and cleared."


def clear_finished_active(soulseek_client, run_async: Callable,
                           sweep_callback: Callable[[], None]) -> bool:
    """Clear all terminal transfers from slskd, sweep dirs on success."""
    success = run_async(soulseek_client.clear_all_completed_downloads())
    if success:
        sweep_callback()
    return success


def clear_completed_local() -> int:
    """Remove completed/failed/cancelled tasks from the local tracker.

    Also prunes batches whose queues are now empty, and removes the matching
    `batch_locks` entry. Returns the number of cleared tasks.
    """
    cleared = 0
    with tasks_lock:
        task_ids_to_remove = [
            tid for tid, task in download_tasks.items()
            if task.get('status') in _TERMINAL_STATUSES
        ]
        for tid in task_ids_to_remove:
            del download_tasks[tid]
            cleared += 1

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

    return cleared
