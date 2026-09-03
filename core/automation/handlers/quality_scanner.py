"""Automation handler: ``start_quality_scan`` action.

The quality scanner was redesigned from an auto-acting tool into the
``quality_upgrade`` library-maintenance repair job (findings-based, reviewed
before anything is wishlisted). This action now simply triggers a "Run Now" of
that job; its progress and findings surface in Library Maintenance. The action
name is kept so existing automation rules keep working.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_start_quality_scan(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')

    # respect_enabled: this is an automation, not someone clicking Run Now.
    # turning the job off in Tools has to mean off, or an import-triggered
    # automation quietly force-runs a weekly scan a dozen times a day (#1207).
    triggered = deps.run_repair_job_now('quality_upgrade', respect_enabled=True)
    if not triggered:
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Skipped',
            log_line='Quality Upgrade Finder is switched off in Tools, skipping',
            log_type='info',
        )
        return {'status': 'skipped', 'reason': 'quality upgrade job is disabled',
                '_manages_own_progress': True}

    deps.update_progress(
        automation_id, status='finished', progress=100, phase='Triggered',
        log_line='Quality Upgrade scan queued — findings appear in Library Maintenance',
        log_type='success',
    )
    return {'status': 'completed', 'triggered': True, '_manages_own_progress': True}
