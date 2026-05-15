"""Automation handler: ``start_quality_scan`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_start_quality_scan`` closure). Submits the quality scanner
to its executor with the configured scope (default: ``watchlist``)
then polls the shared state dict.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from core.automation.deps import AutomationDeps


_TIMEOUT_SECONDS = 7200          # 2 hours
_POLL_INTERVAL_SECONDS = 3
_INITIAL_DELAY_SECONDS = 1


def auto_start_quality_scan(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')
    state = deps.get_quality_scanner_state()
    if state.get('status') == 'running':
        return {'status': 'skipped', 'reason': 'Quality scan already running'}

    scope = config.get('scope', 'watchlist')
    # Pre-set status before submit so the polling loop doesn't see a
    # stale 'finished' from a previous run.
    with deps.quality_scanner_lock:
        state['status'] = 'running'
    deps.quality_scanner_executor.submit(deps.run_quality_scanner, scope, deps.get_current_profile_id())
    deps.update_progress(
        automation_id, log_line=f'Quality scan started (scope: {scope})', log_type='info',
    )

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
            processed=state.get('processed', 0),
            total=state.get('total', 0),
        )
    else:
        deps.update_progress(
            automation_id, status='error',
            phase='Timed out', log_line='Quality scan timed out after 2 hours',
            log_type='error',
        )
        return {'status': 'error', 'reason': 'Timed out', '_manages_own_progress': True}

    final_status = state.get('status', 'idle')
    if final_status == 'error':
        err = state.get('error_message', 'Unknown error')
        deps.update_progress(
            automation_id, status='error', progress=100,
            phase='Error', log_line=err, log_type='error',
        )
        return {'status': 'error', 'reason': err, '_manages_own_progress': True}

    issues = state.get('low_quality', 0)
    deps.update_progress(
        automation_id, status='finished', progress=100,
        phase='Complete',
        log_line=f'Quality scan complete — {issues} issues found',
        log_type='success',
    )
    return {
        'status': 'completed', 'scope': scope, '_manages_own_progress': True,
        'tracks_scanned': state.get('processed', 0),
        'quality_met': state.get('quality_met', 0),
        'low_quality': issues,
        'matched': state.get('matched', 0),
    }
