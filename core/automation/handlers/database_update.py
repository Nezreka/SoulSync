"""Automation handlers: ``start_database_update`` and
``deep_scan_library`` actions.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_start_database_update`` and ``_auto_deep_scan_library``
closures). Both share the same ``db_update_state`` / executor / lock
infrastructure -- the only difference is which task they submit
(``run_db_update_task`` vs ``run_deep_scan_task``).

Pattern: pre-set state to running, submit task to executor, then
poll the state dict until it transitions away from ``running``.
Stall-detection emits a warning every 10 minutes when progress
hasn't budged. 2-hour outer timeout caps the worst case.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from core.automation.deps import AutomationDeps


_TIMEOUT_SECONDS = 7200          # 2 hours — covers the worst large-library case
_STALL_WARNING_SECONDS = 600     # 10 minutes without progress = stall
_POLL_INTERVAL_SECONDS = 3
_INITIAL_DELAY_SECONDS = 1


def auto_start_database_update(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run a full or incremental DB update via ``run_db_update_task``."""
    return _run_with_progress(
        config, deps,
        task=deps.run_db_update_task,
        task_args=(config.get('full_refresh', False), deps.config_manager.get_active_media_server()),
        initial_phase='Initializing...',
        stall_label='Database update',
        finished_extras=lambda: {'full_refresh': str(config.get('full_refresh', False))},
        timeout_label='Database update timed out after 2 hours',
    )


def auto_deep_scan_library(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run a deep library scan via ``run_deep_scan_task``."""
    return _run_with_progress(
        config, deps,
        task=deps.run_deep_scan_task,
        task_args=(deps.config_manager.get_active_media_server(),),
        initial_phase='Deep scan: Initializing...',
        stall_label='Deep scan',
        finished_extras=lambda: {},
        timeout_label='Deep scan timed out after 2 hours',
    )


def _run_with_progress(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    task,
    task_args: tuple,
    initial_phase: str,
    stall_label: str,
    finished_extras,
    timeout_label: str,
) -> Dict[str, Any]:
    """Shared poll-and-wait body for both DB-update handlers."""
    automation_id = config.get('_automation_id')
    state = deps.get_db_update_state()
    if state.get('status') == 'running':
        return {'status': 'skipped', 'reason': 'Database update already running'}
    deps.state.db_update_automation_id = automation_id
    # Sync legacy module global so the DB-update progress callbacks
    # (still living in web_server.py) emit against this automation.
    deps.set_db_update_automation_id(automation_id)

    with deps.db_update_lock:
        state.update({
            'status': 'running', 'phase': initial_phase,
            'progress': 0, 'current_item': '', 'processed': 0, 'total': 0,
            'error_message': '',
        })
    deps.db_update_executor.submit(task, *task_args)

    # Monitor progress (callbacks handle card updates, we just block until done).
    time.sleep(_INITIAL_DELAY_SECONDS)
    poll_start = time.time()
    last_progress_time = time.time()
    last_progress_val = 0
    while time.time() - poll_start < _TIMEOUT_SECONDS:
        time.sleep(_POLL_INTERVAL_SECONDS)
        with deps.db_update_lock:
            current_status = state.get('status', 'idle')
            current_progress = state.get('progress', 0)
        if current_status != 'running':
            break
        # Stall detection — if no progress change in 10 minutes, warn.
        if current_progress != last_progress_val:
            last_progress_val = current_progress
            last_progress_time = time.time()
        elif time.time() - last_progress_time > _STALL_WARNING_SECONDS:
            deps.update_progress(
                automation_id,
                log_line=f'{stall_label} appears stalled — waiting...',
                log_type='warning',
            )
            last_progress_time = time.time()  # Reset so warning repeats every 10 min.
    else:
        # 2-hour timeout reached.
        deps.update_progress(
            automation_id, status='error',
            phase='Timed out', log_line=timeout_label, log_type='error',
        )
        return {'status': 'error', 'reason': 'Timed out', '_manages_own_progress': True}

    # Finished/error callback already updated the card — return matching status.
    with deps.db_update_lock:
        final_status = state.get('status', 'unknown')
    if final_status == 'error':
        return {
            'status': 'error',
            'reason': state.get('error_message', 'Unknown error'),
            '_manages_own_progress': True,
        }
    with deps.db_update_lock:
        stats = {
            'status': 'completed', '_manages_own_progress': True,
            'artists': state.get('total', 0),
            'albums': state.get('total_albums', 0),
            'tracks': state.get('total_tracks', 0),
            'removed_artists': state.get('removed_artists', 0),
            'removed_albums': state.get('removed_albums', 0),
            'removed_tracks': state.get('removed_tracks', 0),
        }
    stats.update(finished_extras())
    return stats
