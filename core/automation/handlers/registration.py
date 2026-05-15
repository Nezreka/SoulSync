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
