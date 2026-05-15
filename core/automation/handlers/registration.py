"""One-stop registration of every extracted automation handler.

``web_server`` builds the deps once at startup and calls
:func:`register_all` here. Each new handler module gets one line in
this file when it lands.
"""

from __future__ import annotations

from core.automation.deps import AutomationDeps
from core.automation.handlers.process_wishlist import auto_process_wishlist
from core.automation.handlers.scan_watchlist import auto_scan_watchlist
from core.automation.handlers.scan_library import auto_scan_library
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist
from core.automation.handlers.discover_playlist import auto_discover_playlist
from core.automation.handlers.playlist_pipeline import auto_playlist_pipeline
from core.automation.handlers.database_update import (
    auto_start_database_update, auto_deep_scan_library,
)
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
from core.automation.handlers.progress_callbacks import (
    progress_init,
    progress_finish,
    record_history,
    register_library_scan_completed_emitter,
)


def register_all(deps: AutomationDeps) -> None:
    """Wire every extracted handler to the engine.

    Each ``register_action_handler`` call binds the action name (the
    string the trigger uses to look up its action) to a thin lambda
    that injects ``deps`` and forwards the engine-supplied config.
    Guards stay alongside their handler so duplicate-run prevention
    behaves identically to the pre-extraction code.
    """
    engine = deps.engine

    # Self-guards prevent duplicate runs of the SAME operation, but
    # different operations can run concurrently — wishlist downloads
    # use bandwidth, watchlist scans use API calls, library scans use
    # media-server CPU. Different resources, no contention.
    engine.register_action_handler(
        'process_wishlist',
        lambda config: auto_process_wishlist(config, deps),
        guard_fn=deps.is_wishlist_actually_processing,
    )
    engine.register_action_handler(
        'scan_watchlist',
        lambda config: auto_scan_watchlist(config, deps),
        guard_fn=deps.is_watchlist_actually_scanning,
    )
    engine.register_action_handler(
        'scan_library',
        lambda config: auto_scan_library(config, deps),
        deps.state.is_scan_library_active,
    )

    # Playlist lifecycle handlers. The pipeline composes refresh +
    # sync + discover (it imports them directly), so all four ship
    # together. The pipeline guard prevents an in-flight pipeline
    # from being re-triggered mid-run.
    engine.register_action_handler(
        'refresh_mirrored',
        lambda config: auto_refresh_mirrored(config, deps),
    )
    engine.register_action_handler(
        'sync_playlist',
        lambda config: auto_sync_playlist(config, deps),
    )
    engine.register_action_handler(
        'discover_playlist',
        lambda config: auto_discover_playlist(config, deps),
    )
    engine.register_action_handler(
        'playlist_pipeline',
        lambda config: auto_playlist_pipeline(config, deps),
        deps.state.is_pipeline_running,
    )

    # Database update + deep scan share the db_update_state guard —
    # only one operation can mutate that state at a time.
    engine.register_action_handler(
        'start_database_update',
        lambda config: auto_start_database_update(config, deps),
        lambda: deps.get_db_update_state().get('status') == 'running',
    )
    engine.register_action_handler(
        'deep_scan_library',
        lambda config: auto_deep_scan_library(config, deps),
        lambda: deps.get_db_update_state().get('status') == 'running',
    )
    engine.register_action_handler(
        'run_duplicate_cleaner',
        lambda config: auto_run_duplicate_cleaner(config, deps),
        lambda: deps.get_duplicate_cleaner_state().get('status') == 'running',
    )
    engine.register_action_handler(
        'clear_quarantine',
        lambda config: auto_clear_quarantine(config, deps),
    )
    engine.register_action_handler(
        'cleanup_wishlist',
        lambda config: auto_cleanup_wishlist(config, deps),
    )
    engine.register_action_handler(
        'update_discovery_pool',
        lambda config: auto_update_discovery_pool(config, deps),
    )
    engine.register_action_handler(
        'start_quality_scan',
        lambda config: auto_start_quality_scan(config, deps),
        lambda: deps.get_quality_scanner_state().get('status') == 'running',
    )
    engine.register_action_handler(
        'backup_database',
        lambda config: auto_backup_database(config, deps),
    )
    engine.register_action_handler(
        'refresh_beatport_cache',
        lambda config: auto_refresh_beatport_cache(config, deps),
    )
    engine.register_action_handler(
        'clean_search_history',
        lambda config: auto_clean_search_history(config, deps),
    )
    engine.register_action_handler(
        'clean_completed_downloads',
        lambda config: auto_clean_completed_downloads(config, deps),
    )
    engine.register_action_handler(
        'full_cleanup',
        lambda config: auto_full_cleanup(config, deps),
    )
    engine.register_action_handler(
        'run_script',
        lambda config: auto_run_script(config, deps),
    )
    engine.register_action_handler(
        'search_and_download',
        lambda config: auto_search_and_download(config, deps),
    )

    # Progress + history callbacks: the engine invokes these around
    # each handler run. Lift the closures from
    # `web_server._register_automation_handlers` into thin lambdas
    # that delegate into the extracted top-level functions.
    engine.register_progress_callbacks(
        lambda aid, name, action_type: progress_init(aid, name, action_type, deps),
        lambda aid, result: progress_finish(aid, result, deps),
        deps.update_progress,
        lambda aid, result: record_history(aid, result, deps),
    )

    # `library_scan_completed` event: when the media-server scan
    # manager finishes a scan, emit the event so any automation can
    # trigger off it. No-op when no scan manager is configured.
    register_library_scan_completed_emitter(deps)
