"""Automation handler: ``scan_library`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_scan_library`` closure). The handler triggers a media-server
scan via ``web_scan_manager``, then polls the manager's status until
the scan completes (or a 30-minute timeout fires). Progress phases
are emitted via :func:`AutomationDeps.update_progress` so the
trigger card stays current throughout the run.

The handler manages its own progress reporting (it sets
``_manages_own_progress: True`` in the result) so the engine doesn't
overwrite the live phase string with a generic 'completed' label.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from core.automation.deps import AutomationDeps


# Outer poll cap — covers extreme worst case (long Plex scans on
# huge libraries). Past this point we surface a clear timeout error
# so users notice rather than letting the trigger hang forever.
_SCAN_TIMEOUT_SECONDS = 1800

# Per-phase poll intervals.
_POLL_SCHEDULED_SECONDS = 2
_POLL_SCANNING_SECONDS = 5
_POLL_UNKNOWN_SECONDS = 2

# Progress percentage waypoints.
_PROGRESS_SCHEDULED_MAX = 14
_PROGRESS_SCAN_START = 15
_PROGRESS_SCAN_MAX = 95


def auto_scan_library(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run a media-server library scan and stream progress to the
    trigger card.

    Returns one of:
      - ``{'status': 'completed', '_manages_own_progress': True, ...}``
      - ``{'status': 'skipped', 'reason': 'Scan already being tracked'}``
      - ``{'status': 'error', 'reason': '...', '_manages_own_progress': True}``
    """
    automation_id = config.get('_automation_id')

    if not deps.web_scan_manager:
        return {'status': 'error', 'reason': 'Scan manager not available'}

    # If another automation is already tracking the scan, just forward
    # the request — the original tracker keeps emitting progress.
    if deps.state.is_scan_library_active():
        deps.web_scan_manager.request_scan('Automation trigger (additional batch)')
        return {'status': 'skipped', 'reason': 'Scan already being tracked'}

    deps.state.set_scan_library_id(automation_id)

    try:
        result = deps.web_scan_manager.request_scan('Automation trigger')
        scan_status_val = result.get('status', 'unknown')

        if scan_status_val == 'queued':
            deps.update_progress(
                automation_id,
                log_line='Scan already in progress — waiting for completion',
                log_type='info',
            )
        else:
            delay = result.get('delay_seconds', 60)
            deps.update_progress(
                automation_id,
                log_line=f'Scan scheduled (debounce: {delay}s)',
                log_type='info',
            )

        # Unified polling loop — handles debounce → scanning → idle.
        poll_start = time.time()
        scan_started = (scan_status_val == 'queued')
        while time.time() - poll_start < _SCAN_TIMEOUT_SECONDS:
            status = deps.web_scan_manager.get_scan_status()
            st = status.get('status')

            if st == 'idle':
                break  # Scan completed (or finished before we polled)

            if st == 'scheduled':
                elapsed = int(time.time() - poll_start)
                deps.update_progress(
                    automation_id,
                    phase=f'Waiting for scan to start... ({elapsed}s)',
                    progress=min(int(elapsed / 60 * 10), _PROGRESS_SCHEDULED_MAX),
                )
                time.sleep(_POLL_SCHEDULED_SECONDS)
                continue

            if st == 'scanning':
                if not scan_started:
                    scan_started = True
                    deps.update_progress(
                        automation_id,
                        progress=_PROGRESS_SCAN_START,
                        log_line='Scan triggered on media server',
                        log_type='success',
                    )
                elapsed = status.get('elapsed_seconds', 0)
                max_time = status.get('max_time_seconds', 300)
                pct = min(_PROGRESS_SCAN_START + int(elapsed / max_time * 80), _PROGRESS_SCAN_MAX)
                mins, secs = divmod(elapsed, 60)
                deps.update_progress(
                    automation_id,
                    phase=f'Library scan in progress... ({mins}m {secs}s)',
                    progress=pct,
                )
                time.sleep(_POLL_SCANNING_SECONDS)
                continue

            time.sleep(_POLL_UNKNOWN_SECONDS)
        else:
            # 30-min timeout reached
            deps.update_progress(
                automation_id,
                status='error',
                phase='Timed out',
                log_line='Library scan timed out after 30 minutes',
                log_type='error',
            )
            return {'status': 'error', 'reason': 'Timed out', '_manages_own_progress': True}

        elapsed = round(time.time() - poll_start, 1)
        deps.update_progress(
            automation_id,
            status='finished',
            progress=100,
            phase='Complete',
            log_line='Library scan completed',
            log_type='success',
        )
        return {
            'status': 'completed',
            '_manages_own_progress': True,
            'scan_duration_seconds': elapsed,
        }

    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        deps.update_progress(
            automation_id,
            status='error',
            phase='Error',
            log_line=str(e),
            log_type='error',
        )
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}

    finally:
        deps.state.set_scan_library_id(None)
