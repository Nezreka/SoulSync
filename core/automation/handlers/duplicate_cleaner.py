"""Automation handler: ``run_duplicate_cleaner`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_run_duplicate_cleaner`` closure). Submits the duplicate
cleaner to its executor, then polls the shared state dict until
the worker transitions away from ``running``.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from core.automation.deps import AutomationDeps


_TIMEOUT_SECONDS = 7200          # 2 hours
_POLL_INTERVAL_SECONDS = 3
_INITIAL_DELAY_SECONDS = 1


def auto_run_duplicate_cleaner(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Kick off the duplicate cleaner and report final stats."""
    automation_id = config.get('_automation_id')
    state = deps.get_duplicate_cleaner_state()
    if state.get('status') == 'running':
        return {'status': 'skipped', 'reason': 'Duplicate cleaner already running'}

    # Pre-set status before submit so the polling loop doesn't see a
    # stale 'finished' from a previous run.
    with deps.duplicate_cleaner_lock:
        state['status'] = 'running'
    deps.duplicate_cleaner_executor.submit(deps.run_duplicate_cleaner)
    deps.update_progress(automation_id, log_line='Duplicate cleaner started', log_type='info')

    # Monitor progress (max 2 hours).
    time.sleep(_INITIAL_DELAY_SECONDS)
    poll_start = time.time()
    while time.time() - poll_start < _TIMEOUT_SECONDS:
        time.sleep(_POLL_INTERVAL_SECONDS)
        current_status = state.get('status', 'idle')
        if current_status not in ('running',):
            break
        deps.update_progress(
            automation_id,
            phase=state.get('phase', 'Scanning...'),
            progress=state.get('progress', 0),
            processed=state.get('files_scanned', 0),
            total=state.get('total_files', 0),
        )
    else:
        # 2-hour timeout reached.
        deps.update_progress(
            automation_id, status='error',
            phase='Timed out',
            log_line='Duplicate cleaner timed out after 2 hours',
            log_type='error',
        )
        return {'status': 'error', 'reason': 'Timed out', '_manages_own_progress': True}

    # Check actual exit status (could be 'finished' or 'error').
    final_status = state.get('status', 'idle')
    if final_status == 'error':
        err = state.get('error_message', 'Unknown error')
        deps.update_progress(
            automation_id, status='error', progress=100,
            phase='Error', log_line=err, log_type='error',
        )
        return {'status': 'error', 'reason': err, '_manages_own_progress': True}

    dupes = state.get('duplicates_found', 0)
    removed = state.get('deleted', 0)
    space_freed = state.get('space_freed', 0)
    scanned = state.get('files_scanned', 0)
    deps.update_progress(
        automation_id, status='finished', progress=100,
        phase='Complete',
        log_line=f'Found {dupes} duplicates, removed {removed} files',
        log_type='success',
    )
    return {
        'status': 'completed', '_manages_own_progress': True,
        'files_scanned': scanned,
        'duplicates_found': dupes,
        'files_deleted': removed,
        'space_freed_mb': round(space_freed / (1024 * 1024), 1),
    }
