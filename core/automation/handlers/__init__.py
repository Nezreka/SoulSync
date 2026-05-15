"""Per-action automation handlers.

Each module in this subpackage exposes one top-level handler function
(or a small cluster of related handlers) of the form::

    def auto_<action_name>(config: dict, deps: AutomationDeps) -> dict

The ``register_all`` helper in :mod:`registration` wires every handler
to the engine in one place. ``web_server.py`` calls
``register_all(deps)`` once at startup.
"""

from core.automation.handlers.process_wishlist import auto_process_wishlist
from core.automation.handlers.scan_watchlist import auto_scan_watchlist
from core.automation.handlers.scan_library import auto_scan_library
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist
from core.automation.handlers.discover_playlist import auto_discover_playlist
from core.automation.handlers.playlist_pipeline import auto_playlist_pipeline
from core.automation.handlers.database_update import auto_start_database_update, auto_deep_scan_library
from core.automation.handlers.duplicate_cleaner import auto_run_duplicate_cleaner
from core.automation.handlers.quality_scanner import auto_start_quality_scan
from core.automation.handlers.maintenance import (
    auto_clear_quarantine,
    auto_cleanup_wishlist,
    auto_update_discovery_pool,
    auto_backup_database,
    auto_refresh_beatport_cache,
)
from core.automation.handlers.download_cleanup import (
    auto_clean_search_history,
    auto_clean_completed_downloads,
    auto_full_cleanup,
)
from core.automation.handlers.run_script import auto_run_script
from core.automation.handlers.search_and_download import auto_search_and_download
from core.automation.handlers.registration import register_all

__all__ = [
    'auto_process_wishlist',
    'auto_scan_watchlist',
    'auto_scan_library',
    'auto_refresh_mirrored',
    'auto_sync_playlist',
    'auto_discover_playlist',
    'auto_playlist_pipeline',
    'auto_start_database_update',
    'auto_deep_scan_library',
    'auto_run_duplicate_cleaner',
    'auto_start_quality_scan',
    'auto_clear_quarantine',
    'auto_cleanup_wishlist',
    'auto_update_discovery_pool',
    'auto_backup_database',
    'auto_refresh_beatport_cache',
    'auto_clean_search_history',
    'auto_clean_completed_downloads',
    'auto_full_cleanup',
    'auto_run_script',
    'auto_search_and_download',
    'register_all',
]
