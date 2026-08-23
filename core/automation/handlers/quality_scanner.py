"""Automation handler: ``start_quality_scan`` action.

There is no quality-scan job left to trigger. Finding a monitored track below
its quality profile's cutoff and queueing the upgrade is not a scheduled scan
any more — it is what the wanted projection does continuously, mirrored into
the Wishlist by ``monitoring_list_reconcile``. The dedicated job did the same
work on a second cadence and was removed.

The action name stays so saved automation rules keep working, and it now runs
the job that actually owns that outcome. Running it on demand is idempotent:
it drains pending mirror ops, reconciles artist monitoring, and re-asserts the
wanted projection into the Wishlist.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_start_quality_scan(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')

    triggered = deps.run_repair_job_now(
        'monitoring_list_reconcile',
        scope={'compatibility_source': 'start_quality_scan'},
    )
    if not triggered:
        deps.update_progress(
            automation_id, status='error', phase='Unavailable',
            log_line='Monitoring List Reconcile could not be triggered (library worker unavailable)',
            log_type='error',
        )
        return {'status': 'error', 'reason': 'library worker unavailable',
                '_manages_own_progress': True}

    deps.update_progress(
        automation_id, status='finished', progress=100, phase='Triggered',
        log_line=(
            'Monitoring List Reconcile queued — missing tracks and upgrade '
            'candidates are re-asserted into the Wishlist'
        ),
        log_type='success',
    )
    return {'status': 'completed', 'triggered': True, '_manages_own_progress': True}
