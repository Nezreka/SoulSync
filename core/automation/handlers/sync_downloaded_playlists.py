"""Automation handler: ``sync_downloaded_playlists`` action.

Automatically syncs playlists whose wishlist download batches recently completed
and whose newly imported tracks have now been indexed in the database.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.sync.post_download_sync import sync_pending_downloaded_playlists

logger = logging.getLogger(__name__)


def auto_sync_downloaded_playlists(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Sync all mirrored playlists that have completed downloads waiting for sync."""
    auto_id = config.get('_automation_id')
    return sync_pending_downloaded_playlists(deps, auto_id=auto_id)
