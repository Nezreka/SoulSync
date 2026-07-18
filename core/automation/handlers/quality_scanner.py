"""Automation handler: ``start_quality_scan`` action.

The quality scanner is now the native Library-v2 ``lib2_upgrade_scan``
repair job (evaluates monitored tracks against their quality profile; the
job's own ``mode`` setting decides automatic queueing vs review findings).
This action simply triggers a "Run Now" of that job; its progress and any
findings surface in Library Maintenance. The action name is kept so existing
automation rules keep working.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_start_quality_scan(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')

    triggered = deps.run_repair_job_now('lib2_upgrade_scan')
    if not triggered:
        deps.update_progress(
            automation_id, status='error', phase='Unavailable',
            log_line='Quality Upgrade job could not be triggered (library worker unavailable)',
            log_type='error',
        )
        return {'status': 'error', 'reason': 'library worker unavailable',
                '_manages_own_progress': True}

    deps.update_progress(
        automation_id, status='finished', progress=100, phase='Triggered',
        log_line='Quality Upgrade scan queued — findings appear in Library Maintenance',
        log_type='success',
    )
    return {'status': 'completed', 'triggered': True, '_manages_own_progress': True}
