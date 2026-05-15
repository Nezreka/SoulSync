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
