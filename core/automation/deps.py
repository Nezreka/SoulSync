"""Dependency-injection surface for automation handlers.

Each handler in ``core.automation.handlers`` is a top-level pure
function that accepts ``(config: dict, deps: AutomationDeps)`` instead
of reaching for module-level globals in ``web_server``. The deps
namespace bundles every callable, client, and mutable-state container
the handlers need.

Construction happens once at app startup in ``web_server.py``:

    from core.automation.deps import AutomationDeps, AutomationState
    state = AutomationState()
    deps = AutomationDeps(
        engine=automation_engine,
        state=state,
        get_database=get_database,
        spotify_client=spotify_client,
        ...
    )
    register_all(deps)

Tests construct a fake ``AutomationDeps`` with stub callables — every
handler is then exercisable without spinning up Flask, the DB, or
the real media-server clients.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class AutomationState:
    """Mutable flags shared across handler invocations.

    Pre-refactor each was a ``global`` or ``nonlocal`` variable inside
    the registration closure. Lifted here so handlers + their guards
    can read/write a single object instead of importing globals.

    All mutations should hold ``lock``; the helper methods below do
    so for the common get/set patterns.
    """

    scan_library_automation_id: Optional[str] = None
    db_update_automation_id: Optional[str] = None
    pipeline_running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def is_scan_library_active(self) -> bool:
        with self.lock:
            return self.scan_library_automation_id is not None

    def is_pipeline_running(self) -> bool:
        with self.lock:
            return self.pipeline_running

    def set_scan_library_id(self, automation_id: Optional[str]) -> None:
        with self.lock:
            self.scan_library_automation_id = automation_id

    def set_pipeline_running(self, value: bool) -> None:
        with self.lock:
            self.pipeline_running = value


@dataclass
class AutomationDeps:
    """Bundle of every callable + client an automation handler may need.

    Add fields as new handlers are extracted. Every field is required
    at construction (no defaults) so a missing dep fails loudly at
    startup, not silently mid-handler.
    """

    # --- Engine + shared state ---
    engine: Any                                          # AutomationEngine instance
    state: AutomationState
    config_manager: Any                                  # config.settings.ConfigManager singleton
    update_progress: Callable[..., None]                 # _update_automation_progress
    logger: Any                                          # module-level logger from utils.logging_config

    # --- Service clients (each may be None depending on user config) ---
    get_database: Callable[[], Any]                      # late-binding so tests don't need DB
    spotify_client: Any
    tidal_client: Any
    web_scan_manager: Any

    # --- Background-task entry points ---
    process_wishlist_automatically: Callable[..., Any]
    process_watchlist_scan_automatically: Callable[..., Any]
    is_wishlist_actually_processing: Callable[[], bool]
    is_watchlist_actually_scanning: Callable[[], bool]
    get_watchlist_scan_state: Callable[[], dict]         # accessor returns the live mutable dict

    # --- Playlist pipeline entry points ---
    run_playlist_discovery_worker: Callable[..., Any]
    run_sync_task: Callable[..., Any]
    load_sync_status_file: Callable[[], dict]
    get_deezer_client: Callable[[], Any]
    parse_youtube_playlist: Callable[[str], Any]
    get_sync_states: Callable[[], dict]                  # accessor returns the live dict shared with the sync UI

    # --- Database update + quality scanner (shared state + executors) ---
    set_db_update_automation_id: Callable[[Optional[str]], None]  # syncs the legacy `_db_update_automation_id` global so the live DB-update progress callbacks (which still read the global directly) keep firing for the active automation
    get_db_update_state: Callable[[], dict]
    db_update_lock: Any                                  # threading.Lock
    db_update_executor: Any                              # ThreadPoolExecutor
    run_db_update_task: Callable[..., Any]
    run_deep_scan_task: Callable[..., Any]
    get_duplicate_cleaner_state: Callable[[], dict]
    duplicate_cleaner_lock: Any
    duplicate_cleaner_executor: Any
    run_duplicate_cleaner: Callable[..., Any]
    get_quality_scanner_state: Callable[[], dict]
    quality_scanner_lock: Any
    quality_scanner_executor: Any
    run_quality_scanner: Callable[..., Any]

    # --- Download orchestrator + queue accessors ---
    download_orchestrator: Any
    run_async: Callable[..., Any]
    tasks_lock: Any
    get_download_batches: Callable[[], dict]
    get_download_tasks: Callable[[], dict]
    sweep_empty_download_directories: Callable[[], int]
    get_staging_path: Callable[[], str]

    # --- Maintenance helpers ---
    docker_resolve_path: Callable[[str], str]
    get_current_profile_id: Callable[[], int]
    get_watchlist_scanner: Callable[[Any], Any]
    get_app: Callable[[], Any]                           # Flask app for test_client (beatport refresh)
    get_beatport_data_cache: Callable[[], dict]

    # --- Progress + history callbacks (used by register_all to wire
    # the engine's progress callback hooks). ---
    init_automation_progress: Callable[..., Any]
    record_progress_history: Callable[..., Any]

    # --- Personalized playlist pipeline ---
    # Lazy builder so the pipeline handler can construct a fresh
    # PersonalizedPlaylistManager per run (cheap accessors inside,
    # no caching needed yet).
    build_personalized_manager: Callable[[], Any]
