"""Which server playlists each library track sits in.

the duplicate detector uses this so it can keep the copy a playlist points at
(discord, jadux: 500 duplicates, wants to bulk accept without breaking
playlists). library track ids ARE the server's ids (tracks.id is the plex
ratingKey / jellyfin Id / navidrome id), so a playlist's track ids line up with
library rows directly.

only sees playlists the connected server account can see. best effort: any
failure is an empty map, never a failed scan or fix.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library.playlist_membership")

# a bulk "keep best" over 500 findings asks once per finding. one read of the
# server per few minutes is plenty, playlists don't change that fast.
_CACHE_SECONDS = 300

_lock = threading.Lock()
_cache: Dict[str, Any] = {'at': 0.0, 'server': None, 'map': {}}


def _playlist_track_ids(server: str, client: Any, playlist: Any) -> List[str]:
    playlist_id = getattr(playlist, 'id', None)
    if not playlist_id:
        return []
    if server == 'navidrome':
        tracks = client.get_playlist_tracks(playlist_id) or []
        return [str(t.ratingKey) for t in tracks if getattr(t, 'ratingKey', None)]
    if server == 'plex':
        return client.get_playlist_track_ids(playlist_id, getattr(playlist, 'title', '') or '')
    if server == 'jellyfin':
        return client.get_playlist_track_ids(playlist_id)
    return []


def read_server_playlist_membership(server: str, client: Any) -> Dict[str, List[str]]:
    """{track_id: [playlist titles]} for every playlist the client can see."""
    membership: Dict[str, List[str]] = {}
    if server not in ('navidrome', 'plex', 'jellyfin') or client is None:
        return membership
    try:
        playlists = client.get_all_playlists() or []
    except Exception as e:
        logger.warning("Could not list %s playlists: %s", server, e)
        return membership
    for playlist in playlists:
        title = getattr(playlist, 'title', '') or 'Untitled'
        try:
            ids = _playlist_track_ids(server, client, playlist)
        except Exception as e:
            logger.debug("Could not read %s playlist %r: %s", server, title, e)
            continue
        for track_id in ids:
            names = membership.setdefault(str(track_id), [])
            if title not in names:
                names.append(title)
    return membership


def _active_server_and_client():
    # only the engine web_server installed at boot. building one here would
    # spin up every server client from a repair thread (or a test)
    from core.media_server import engine as engine_module
    engine = engine_module._default_engine
    if engine is None:
        return None, None
    return engine.active_server, engine.active_client()


def server_playlist_membership(
    *,
    now: Optional[Callable[[], float]] = None,
    resolve: Optional[Callable[[], tuple]] = None,
) -> Dict[str, List[str]]:
    """Cached membership for the active media server."""
    clock = now or time.monotonic
    try:
        server, client = (resolve or _active_server_and_client)()
    except Exception as e:
        logger.debug("No media server for playlist membership: %s", e)
        return {}
    with _lock:
        fresh = clock() - _cache['at'] < _CACHE_SECONDS
        if fresh and _cache['server'] == server:
            return _cache['map']
    membership = read_server_playlist_membership(server, client)
    with _lock:
        _cache.update(at=clock(), server=server, map=membership)
    return membership


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, server=None, map={})


def tag_tracks(tracks: List[Dict[str, Any]], membership: Dict[str, List[str]]) -> None:
    """Set each track dict's 'playlists' from the map (by track_id or id)."""
    for track in tracks:
        track_id = track.get('track_id') or track.get('id')
        track['playlists'] = list(membership.get(str(track_id), [])) if track_id is not None else []
