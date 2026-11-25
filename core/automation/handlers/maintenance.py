"""Automation handlers: short maintenance actions.

Lifted from ``web_server._register_automation_handlers``:
- ``clear_quarantine``       → :func:`auto_clear_quarantine`
- ``cleanup_wishlist``       → :func:`auto_cleanup_wishlist`
- ``update_discovery_pool``  → :func:`auto_update_discovery_pool`
- ``backup_database``        → :func:`auto_backup_database`
- ``refresh_beatport_cache`` → :func:`auto_refresh_beatport_cache`

Each is a thin wrapper around an existing service / helper. Grouped
in one module because every body is short and they share no state
between them — splitting into per-handler files would just add
import noise.
"""

from __future__ import annotations

import glob as _glob
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict

from core.automation.deps import AutomationDeps


# ─── clear_quarantine ────────────────────────────────────────────────


def auto_clear_quarantine(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Purge every file/folder under the configured ss_quarantine path."""
    automation_id = config.get('_automation_id')
    from core.library.cleanup import clear_quarantine_folder, quarantine_path as _qpath
    result = clear_quarantine_folder(_qpath(
        deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads'))))
    if not result['existed']:
        deps.update_progress(automation_id, log_line='No quarantine folder found', log_type='info')
        return {'status': 'completed', 'removed': '0'}
    removed = result['removed']
    deps.update_progress(
        automation_id,
        log_line=f'Removed {removed} quarantined items',
        log_type='success' if removed > 0 else 'info',
    )
    return {'status': 'completed', 'removed': str(removed)}


# ─── cleanup_wishlist ────────────────────────────────────────────────


def auto_cleanup_wishlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Drop duplicate entries from the wishlist for the active profile."""
    automation_id = config.get('_automation_id')
    db = deps.get_database()
    removed = db.remove_wishlist_duplicates(deps.get_current_profile_id())
    deps.update_progress(
        automation_id,
        log_line=f'Removed {removed or 0} duplicate wishlist entries',
        log_type='success' if removed else 'info',
    )
    return {'status': 'completed', 'removed': str(removed or 0)}


# ─── update_discovery_pool ───────────────────────────────────────────


def auto_update_discovery_pool(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run an incremental refresh of the discovery pool via the
    watchlist scanner.

    A manual Run Now press (``_manual_run``, #1719) forces the refresh
    past the scanner's 6-hour cooldown — the user pressed it precisely
    so a refresh happens now, e.g. to apply changed settings. Scheduled
    and event-triggered runs keep the cooldown."""
    automation_id = config.get('_automation_id')
    try:
        manual = bool(config.get('_manual_run'))
        scanner = deps.get_watchlist_scanner(deps.spotify_client)
        deps.update_progress(automation_id, log_line='Updating discovery pool...', log_type='info')
        scanner.update_discovery_pool_incremental(deps.get_current_profile_id(), force=manual)
        deps.update_progress(
            automation_id, status='finished', progress=100,
            phase='Complete', log_line='Discovery pool updated', log_type='success',
        )
        return {'status': 'completed', '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise
        deps.update_progress(
            automation_id, status='error',
            phase='Error', log_line=str(e), log_type='error',
        )
        return {'status': 'error', 'reason': str(e), '_manages_own_progress': True}


# ─── backup_database ─────────────────────────────────────────────────


_MAX_BACKUPS = 5


def _backup_db_at(db_path: str, deps: AutomationDeps, automation_id) -> Dict[str, Any]:
    """Create a hot SQLite backup of ``db_path``, then prune old backups so only
    the newest ``_MAX_BACKUPS`` remain. Shared by the music and video backup
    actions — only the DB file differs (they can't share one backup)."""
    if not os.path.exists(db_path):
        return {'status': 'error', 'reason': 'Database file not found'}

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.backup_{timestamp}"
    # safe_backup verifies source + result integrity, so an automated backup
    # can never silently snapshot a corrupt DB (the incident where every
    # rolling backup faithfully copied the corruption).
    from core.db_integrity import DBIntegrityError, backup_lock, room_for, rotate_backups, safe_backup
    short = room_for(db_path, os.path.getsize(db_path))
    if short:
        deps.update_progress(automation_id, log_line=f'Backup SKIPPED: {short}', log_type='error')
        return {'status': 'error', 'reason': short}
    try:
        # one backup or restore at a time; a restore in progress is waited for
        with backup_lock:
            safe_backup(db_path, backup_path)
    except DBIntegrityError as integ:
        deps.logger.error("Auto-backup refused — DB integrity check failed: %s", integ)
        # said where an admin will see it, not only in the log: a damaged
        # database stopped every backup for two weeks before anyone knew
        _database_damaged(str(integ))
        deps.update_progress(
            automation_id,
            log_line=f'Backup SKIPPED — database failed integrity check: {integ}',
            log_type='error',
        )
        return {'status': 'error', 'reason': f'Database integrity check failed: {integ}'}
    try:
        from core import db_repair
        db_repair.remember(True)
    except Exception as e:  # noqa: BLE001
        deps.logger.debug("database health not remembered: %s", e)
    size_mb = round(os.path.getsize(backup_path) / (1024 * 1024), 1)

    # Rolling cleanup — never evict the most-recent verified-healthy backup.
    # counted in backups: the glob this used also matched each backup's -wal,
    # -shm and .meta.json, so "keep 5" kept one
    rotate_backups(db_path, _MAX_BACKUPS)
    deps.update_progress(
        automation_id,
        log_line=f'Backup created: {size_mb}MB ({os.path.basename(backup_path)})',
        log_type='success',
    )
    return {'status': 'completed', 'backup_path': backup_path, 'size_mb': str(size_mb)}


def auto_backup_database(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Hot SQLite backup of the MUSIC database (``DATABASE_PATH``)."""
    db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
    result = _backup_db_at(db_path, deps, config.get('_automation_id'))
    if result.get('status') == 'completed':
        # the player's and the audiobooks' databases ride along with it
        from core.backup_companions import backup_companions
        stamp = os.path.basename(result['backup_path']).rsplit('.backup_', 1)[-1]
        for also in backup_companions(stamp, _MAX_BACKUPS):
            deps.update_progress(
                config.get('_automation_id'),
                log_line=(f"Also backed up the {also['name']} database ({also['file']})" if also['ok']
                          else f"The {also['name']} database was NOT backed up: {also['error']}"),
                log_type='success' if also['ok'] else 'error',
            )
    return result


def auto_backup_video_database(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Hot SQLite backup of the VIDEO database (``VIDEO_DATABASE_PATH`` /
    video_library.db). Same logic as the music backup but a different DB file —
    the two can't share one backup, so this is a video-specific action."""
    db_path = os.environ.get('VIDEO_DATABASE_PATH', 'database/video_library.db')
    return _backup_db_at(db_path, deps, config.get('_automation_id'))


# ─── refresh_beatport_cache ──────────────────────────────────────────


_BEATPORT_SECTIONS = (
    ('hero_tracks', '/api/beatport/hero-tracks', 'Hero Tracks'),
    ('new_releases', '/api/beatport/new-releases', 'New Releases'),
    ('featured_charts', '/api/beatport/featured-charts', 'Featured Charts'),
    ('dj_charts', '/api/beatport/dj-charts', 'DJ Charts'),
    ('top_10_lists', '/api/beatport/homepage/top-10-lists', 'Top 10 Lists'),
    ('top_10_releases', '/api/beatport/homepage/top-10-releases-cards', 'Top 10 Releases'),
    ('hype_picks', '/api/beatport/hype-picks', 'Hype Picks'),
)


def auto_refresh_beatport_cache(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Refresh Beatport homepage cache by calling each endpoint internally
    via Flask's ``test_client``. Invalidates the homepage cache first
    so endpoints re-scrape rather than returning stale data."""
    automation_id = config.get('_automation_id')
    cache = deps.get_beatport_data_cache()
    # Invalidate all homepage cache timestamps so endpoints re-scrape.
    with cache['cache_lock']:
        for key in cache['homepage']:
            cache['homepage'][key]['timestamp'] = 0
            cache['homepage'][key]['data'] = None

    refreshed = 0
    errors = []
    app = deps.get_app()
    with app.test_client() as client:
        for idx, (_, endpoint, label) in enumerate(_BEATPORT_SECTIONS):
            deps.update_progress(
                automation_id,
                progress=(idx / len(_BEATPORT_SECTIONS)) * 100,
                phase=f'Scraping: {label}',
                current_item=label,
            )
            try:
                resp = client.get(endpoint)
                if resp.status_code == 200:
                    refreshed += 1
                    deps.update_progress(
                        automation_id, log_line=f'{label}: cached', log_type='success',
                    )
                else:
                    errors.append(label)
                    deps.update_progress(
                        automation_id,
                        log_line=f'{label}: HTTP {resp.status_code}',
                        log_type='error',
                    )
            except Exception as e:  # noqa: BLE001 — per-section best-effort
                errors.append(label)
                deps.update_progress(
                    automation_id,
                    log_line=f'{label}: {str(e)}',
                    log_type='error',
                )
            if idx < len(_BEATPORT_SECTIONS) - 1:
                time.sleep(2)

    deps.update_progress(
        automation_id, status='finished', progress=100,
        phase='Complete',
        log_line=f'Refreshed {refreshed}/{len(_BEATPORT_SECTIONS)} sections',
        log_type='success',
    )
    return {
        'status': 'completed',
        'refreshed': str(refreshed),
        'errors': str(len(errors)),
        '_manages_own_progress': True,
    }


def _database_damaged(detail: str) -> None:
    """remember it, and tell every admin once a day: the bell and the
    Backups page both say so, with the way to repair it"""
    try:
        from core import db_repair
        before = db_repair.last_health()
        db_repair.remember(False, detail)
        if before.get("healthy") is False and before.get("notified_day") == time.strftime("%Y-%m-%d"):
            return
        from core.profile_notify import notify_profile
        from database.music_database import get_database
        for p in get_database().get_all_profiles() or []:
            if p.get("is_admin"):
                notify_profile(p["id"], "Your database is damaged, so backups have stopped. "
                               "Open Settings, System, Backups to repair it.", "error",
                               link="/settings?tab=advanced&view=backups")
        with db_repair._health_lock:
            db_repair._health["notified_day"] = time.strftime("%Y-%m-%d")
    except Exception as e:  # noqa: BLE001 - the backup's own error is already logged
        import logging
        logging.getLogger(__name__).debug("damaged-database warning not sent: %s", e)
