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
import shutil as _shutil
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict

from core.automation.deps import AutomationDeps


# ─── clear_quarantine ────────────────────────────────────────────────


def auto_clear_quarantine(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Purge every file/folder under the configured ss_quarantine path."""
    automation_id = config.get('_automation_id')
    quarantine_path = os.path.join(
        deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads')),
        'ss_quarantine',
    )
    if not os.path.exists(quarantine_path):
        deps.update_progress(automation_id, log_line='No quarantine folder found', log_type='info')
        return {'status': 'completed', 'removed': '0'}
    removed = 0
    for f in os.listdir(quarantine_path):
        fp = os.path.join(quarantine_path, f)
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                removed += 1
            elif os.path.isdir(fp):
                _shutil.rmtree(fp)
                removed += 1
        except Exception as e:  # noqa: BLE001 — best-effort purge
            deps.logger.debug("quarantine entry purge failed: %s", e)
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
    watchlist scanner."""
    automation_id = config.get('_automation_id')
    try:
        scanner = deps.get_watchlist_scanner(deps.spotify_client)
        deps.update_progress(automation_id, log_line='Updating discovery pool...', log_type='info')
        scanner.update_discovery_pool_incremental(deps.get_current_profile_id())
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


def auto_backup_database(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Create a hot SQLite backup, then prune old backups so only the
    newest ``_MAX_BACKUPS`` remain."""
    automation_id = config.get('_automation_id')
    db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
    if not os.path.exists(db_path):
        return {'status': 'error', 'reason': 'Database file not found'}

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.backup_{timestamp}"
    # Use SQLite backup API for a safe hot-copy of an active database.
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close()
    src.close()
    size_mb = round(os.path.getsize(backup_path) / (1024 * 1024), 1)

    # Rolling cleanup — keep only the newest N backups.
    existing = sorted(_glob.glob(f"{db_path}.backup_*"), key=os.path.getmtime)
    while len(existing) > _MAX_BACKUPS:
        try:
            os.remove(existing.pop(0))
        except Exception as e:  # noqa: BLE001 — best-effort cleanup
            deps.logger.debug("rolling backup cleanup failed: %s", e)
    deps.update_progress(
        automation_id,
        log_line=f'Backup created: {size_mb}MB ({os.path.basename(backup_path)})',
        log_type='success',
    )
    return {'status': 'completed', 'backup_path': backup_path, 'size_mb': str(size_mb)}


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
