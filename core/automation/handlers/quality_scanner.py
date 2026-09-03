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

    # respect_enabled: this is an automation, not someone clicking Run Now.
    # turning the job off in Tools has to mean off, or an import-triggered
    # automation quietly force-runs a weekly job a dozen times a day (#1207).
    # Upstream applied this to `quality_upgrade`; the job that owns the outcome
    # here is `monitoring_list_reconcile`, and it is toggleable in Tools too.
    triggered = deps.run_repair_job_now(
        'monitoring_list_reconcile',
        scope={'compatibility_source': 'start_quality_scan'},
        respect_enabled=True,
    )
    if not triggered:
        # Both refusals — switched off, and no library worker — report as
        # skipped. #1192: this automation used to cry wolf on every run, and a
        # deliberate toggle is not a fault either way.
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Skipped',
            log_line='Monitoring List Reconcile is switched off in Tools, skipping',
            log_type='info',
        )
        return {'status': 'skipped', 'reason': 'monitoring list reconcile job is disabled',
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
