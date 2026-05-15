"""Automation handler: ``discover_playlist`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_discover_playlist`` closure). Kicks off background discovery
of official Spotify / iTunes metadata for mirrored playlist tracks.
The worker runs in a daemon thread and emits its own progress; this
handler returns immediately after launching it (``_manages_own_progress``).
"""

from __future__ import annotations

import threading
from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_discover_playlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Discover official Spotify/iTunes metadata for mirrored
    playlist tracks. Runs the worker in a background thread."""
    db = deps.get_database()
    playlist_id = config.get('playlist_id')
    discover_all = config.get('all', False)

    if discover_all:
        playlists = db.get_mirrored_playlists()
    elif playlist_id:
        p = db.get_mirrored_playlist(int(playlist_id))
        playlists = [p] if p else []
    else:
        return {'status': 'error', 'reason': 'No playlist specified'}

    if not playlists:
        return {'status': 'error', 'reason': 'No playlists found'}

    threading.Thread(
        target=deps.run_playlist_discovery_worker,
        args=(playlists, config.get('_automation_id')),
        daemon=True,
        name='auto-discover-playlist',
    ).start()
    names = ', '.join(p['name'] for p in playlists[:3])
    return {
        'status': 'started',
        'playlist_count': str(len(playlists)),
        'playlists': names,
        '_manages_own_progress': True,
    }
