"""Automation handler: ``scan_watchlist`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_scan_watchlist`` closure). The watchlist scanner returns
summary stats for the trigger card only when a fresh scan actually
ran — detected by snapshotting ``id(state_dict)`` before/after, since
the live processor reassigns the dict on each new scan.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_scan_watchlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run a watchlist scan when the automation triggers.

    Pre-scan we capture ``id(watchlist_scan_state)`` so we can tell
    afterwards whether the worker ran (and reassigned the state dict)
    or short-circuited (kept the same dict). Only fresh scans report
    summary stats — repeat triggers without an intervening run return
    a bare ``completed``.
    """
    try:
        pre_state = deps.get_watchlist_scan_state()
        pre_state_id = id(pre_state)
        deps.process_watchlist_scan_automatically(
            automation_id=config.get('_automation_id'),
            profile_id=config.get('_profile_id'),
        )
        post_state = deps.get_watchlist_scan_state()
        # Fresh scan = state dict was reassigned mid-run.
        if id(post_state) != pre_state_id:
            summary = post_state.get('summary', {}) if isinstance(post_state, dict) else {}
            return {
                'status': 'completed',
                'artists_scanned': summary.get('total_artists', 0),
                'successful_scans': summary.get('successful_scans', 0),
                'new_tracks_found': summary.get('new_tracks_found', 0),
                'tracks_added_to_wishlist': summary.get('tracks_added_to_wishlist', 0),
            }
        return {'status': 'completed'}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        return {'status': 'error', 'error': str(e)}
