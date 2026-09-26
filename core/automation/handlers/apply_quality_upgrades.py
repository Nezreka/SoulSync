"""Automation handler: ``apply_quality_upgrades`` action.

The Quality Upgrade Finder files findings; applying one queues the better
version on the wishlist. This action applies them on a schedule, for profiles
that asked to keep upgrading until their cutoff. Nothing runs unless someone
adds the action to an automation.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.quality.upgrades import until_cutoff_finding_ids

DEFAULT_LIMIT = 100


def auto_apply_quality_upgrades(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')
    try:
        limit = max(1, int(config.get('limit') or DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    ids = until_cutoff_finding_ids(deps.get_database(), limit=limit)
    if not ids:
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Nothing to apply',
            log_line='No pending upgrades for profiles set to "upgrade until cutoff"',
            log_type='info',
        )
        return {'status': 'completed', 'applied': 0, '_manages_own_progress': True}

    if deps.bulk_fix_repair_findings is None:
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Skipped',
            log_line='Library Maintenance is not running, nothing applied',
            log_type='info',
        )
        return {'status': 'skipped', 'reason': 'repair worker unavailable',
                '_manages_own_progress': True}

    result = deps.bulk_fix_repair_findings(ids) or {}
    fixed = int(result.get('fixed') or 0)
    failed = int(result.get('failed') or 0)
    deps.update_progress(
        automation_id, status='finished', progress=100, phase='Applied',
        log_line=(f'Queued {fixed} quality upgrade{"s" if fixed != 1 else ""} on the wishlist'
                  + (f', {failed} could not be queued' if failed else '')),
        log_type='success' if not failed else 'warning',
    )
    return {'status': 'completed', 'applied': fixed, 'failed': failed,
            '_manages_own_progress': True}
