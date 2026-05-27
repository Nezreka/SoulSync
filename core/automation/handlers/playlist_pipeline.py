"""Automation adapter for the mirrored playlist pipeline.

The actual all-in-one playlist lifecycle lives in
``core.playlists.pipeline`` so it can be reused by non-automation UI actions.
This module only wires automation-specific dependencies and handlers.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist
from core.playlists.pipeline import run_mirrored_playlist_pipeline


def auto_playlist_pipeline(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run REFRESH -> DISCOVER -> SYNC -> WISHLIST for mirrored playlists."""
    return run_mirrored_playlist_pipeline(
        config,
        deps,
        refresh_fn=auto_refresh_mirrored,
        sync_one_fn=auto_sync_playlist,
        sync_and_wishlist_fn=run_sync_and_wishlist,
    )
