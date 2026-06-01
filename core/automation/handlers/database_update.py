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


# Time out on STALL (no progress), not total runtime: a large library can scan
# for many hours while progressing fine — a hard total cap would falsely mark a
# healthy scan 'error' (the scan thread keeps running uncancelled). We only give
# up when progress hasn't moved for a long stretch, with a generous absolute
# backstop against a truly stuck monitor loop.
_STALL_WARNING_SECONDS = 600     # warn after 10 min with no progress (repeats)
_STALL_TIMEOUT_SECONDS = 1800    # 30 min with no progress at all = genuinely stalled
_ABSOLUTE_CAP_SECONDS = 86400    # 24h hard backstop (runaway-loop guard only)
_POLL_INTERVAL_SECONDS = 3
_INITIAL_DELAY_SECONDS = 1


def scan_wait_action(
    *,
    status: str,
    idle_seconds: float,
    total_seconds: float,
    stall_timeout_s: float = _STALL_TIMEOUT_SECONDS,
    stall_warn_s: float = _STALL_WARNING_SECONDS,
    abs_cap_s: float = _ABSOLUTE_CAP_SECONDS,
) -> str:
    """Decide what the monitor loop should do on a poll tick (pure/testable).

    ``idle_seconds`` is time since progress last changed; ``total_seconds`` is
    time since the wait began. Returns one of:
    ``'finished'`` (task no longer running), ``'stall_timeout'`` (no progress for
    too long → give up), ``'abs_timeout'`` (absolute backstop), ``'warn'``
    (stalled long enough to warn but not give up), or ``'continue'``.

    Crucially, an actively-progressing scan keeps resetting ``idle_seconds``, so
    it never hits ``stall_timeout`` no matter how long the whole scan takes.
    """
    if status != 'running':
        return 'finished'
    if total_seconds >= abs_cap_s:
        return 'abs_timeout'
    if idle_seconds >= stall_timeout_s:
        return 'stall_timeout'
    if idle_seconds >= stall_warn_s:
        return 'warn'
    return 'continue'


def auto_start_database_update(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run a full or incremental DB update via ``run_db_update_task``."""
    return _run_with_progress(
        config, deps,
        task=deps.run_db_update_task,
        task_args=(config.get('full_refresh', False), deps.config_manager.get_active_media_server()),
        initial_phase='Initializing...',
        stall_label='Database update',
        finished_extras=lambda: {'full_refresh': str(config.get('full_refresh', False))},
        timeout_label='Database update timed out after 24 hours',
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
        timeout_label='Deep scan timed out after 24 hours',
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
    # We time out on STALL, not total runtime: ``processed`` advances on every
    # artist, so an actively-progressing scan keeps resetting the idle clock and
    # is never falsely failed no matter how long the whole library takes.
    time.sleep(_INITIAL_DELAY_SECONDS)
    poll_start = time.time()
    last_progress_time = time.time()
    # Any of these advancing means the scan is alive. current_item (the artist
    # being processed) changes every artist even when the rounded progress %
    # holds steady, so it guards against a false stall during slow stretches.
    last_progress_val = (0, 0, '')
    last_warn_time = 0.0
    outcome = 'finished'
    while True:
        time.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        with deps.db_update_lock:
            current_status = state.get('status', 'idle')
            current_val = (state.get('processed', 0), state.get('progress', 0),
                           state.get('current_item', ''))
        if current_val != last_progress_val:
            last_progress_val = current_val
            last_progress_time = now

        action = scan_wait_action(
            status=current_status,
            idle_seconds=now - last_progress_time,
            total_seconds=now - poll_start,
        )
        if action in ('finished', 'stall_timeout', 'abs_timeout'):
            outcome = action
            break
        if action == 'warn' and (now - last_warn_time) > _STALL_WARNING_SECONDS:
            idle_min = int((now - last_progress_time) / 60)
            deps.update_progress(
                automation_id,
                log_line=f'{stall_label} — no progress for {idle_min} min, still waiting...',
                log_type='warning',
            )
            last_warn_time = now

    if outcome == 'stall_timeout':
        deps.update_progress(
            automation_id, status='error', phase='Stalled',
            log_line=f'{stall_label} made no progress for {_STALL_TIMEOUT_SECONDS // 60} minutes — giving up',
            log_type='error',
        )
        return {'status': 'error', 'reason': 'Stalled (no progress)', '_manages_own_progress': True}
    if outcome == 'abs_timeout':
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
