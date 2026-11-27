"""Automation handler: ``apply_quality_upgrades`` action.

The native quality audit files proposals. This action applies current proposals
for already-wanted tracks in the automation owner's library, whose live profile
asks to keep upgrading until cutoff. Unmonitored audit subjects stay for review.
Preserved pre-v2 findings remain actionable for the shared library.

Albums whose profile upgrades whole albums are not applied track by track:
after the per-track pass, up to ``album_limit`` of them are upgraded as one
release each (core.library2.album_upgrade), or left as they are with the
reason in this run's log.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.quality.upgrades import until_cutoff_finding_ids

DEFAULT_LIMIT = 100
DEFAULT_ALBUM_LIMIT = 5


def auto_apply_quality_upgrades(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')
    try:
        limit = max(1, int(config.get('limit') or DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    owner = int(config.get('_profile_id') or getattr(deps, 'get_current_profile_id', lambda: 1)())
    albums = _apply_whole_album_upgrades(config, deps, automation_id, owner)
    ids = until_cutoff_finding_ids(deps.get_database(), limit=limit, profile_id=owner)
    if not ids and albums is not None:
        upgraded = sum(1 for o in albums if o.upgraded)
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Applied',
            log_line=(f'Upgraded {upgraded} of {len(albums)} album'
                      f'{"s" if len(albums) != 1 else ""} as whole releases'),
            log_type='success' if upgraded else 'info',
        )
        return {'status': 'completed', 'applied': 0, 'albums_upgraded': upgraded,
                'albums_tried': len(albums), '_manages_own_progress': True}
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

    result = deps.bulk_fix_repair_findings(
        ids, fix_action=f'scheduled_quality_upgrade:{owner}',
    ) or {}
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


def _apply_whole_album_upgrades(config, deps, automation_id, owner):
    """The whole-album upgrades due for this owner, or None when there were none.

    Each album is its own log line: upgraded, or left as it is and why."""
    from core.library2 import album_upgrade

    try:
        album_limit = max(0, int(config.get('album_limit', DEFAULT_ALBUM_LIMIT)))
    except (TypeError, ValueError):
        album_limit = DEFAULT_ALBUM_LIMIT
    if not album_limit:
        return None
    database = deps.get_database()
    try:
        conn = database._get_connection()
    except Exception as exc:  # noqa: BLE001 - no library to look at: the per-track pass decides
        deps.logger.debug('whole-album upgrade lookup skipped: %s', exc)
        return None
    try:
        pending = album_upgrade.pending_album_ids(conn, profile_id=owner, limit=album_limit)
    except Exception as exc:  # noqa: BLE001 - the per-track pass still runs
        deps.logger.warning('whole-album upgrade lookup failed: %s', exc)
        pending = []
    finally:
        conn.close()
    if not pending:
        return None

    def _say(line):
        deps.update_progress(automation_id, phase='Upgrading whole albums', log_line=line,
                             log_type='info')

    def _rescan(_outcome):
        scanner = getattr(deps, 'web_scan_manager', None)
        if scanner is not None and hasattr(scanner, 'request_scan'):
            scanner.request_scan('Whole-album quality upgrade')

    outcomes = album_upgrade.run_album_upgrades(
        database, profile_id=owner, album_ids=pending, progress=_say, on_upgraded=_rescan)
    for outcome in outcomes:
        deps.update_progress(automation_id, log_line=outcome.log_line(),
                             log_type='success' if outcome.upgraded else 'warning')
    return outcomes
