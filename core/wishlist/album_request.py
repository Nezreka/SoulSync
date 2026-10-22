"""put a whole album on a profile's wishlist, for callers outside the ui.

the lidarr api and the mcp tools both ask for albums. they go through the same
add the album modal uses (owned-track skip, ignore list, dedupe), so a request
from outside acts exactly like a click inside.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict


def queue_album_tracks(album: Dict[str, Any], artist: Dict[str, Any], *, profile_id: int,
                       get_database: Callable, active_server: str, logger,
                       requested_via: str) -> int:
    """album is the album-modal shape with a "tracks" list. returns how many
    tracks went on the wishlist (owned and already-queued ones don't count)."""
    from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope
    from core.wishlist.routes import WishlistRouteRuntime, add_album_track_to_wishlist

    runtime = WishlistRouteRuntime(
        get_music_database=get_database, profile_id=profile_id, download_batches={}, download_tasks={},
        tasks_lock=threading.Lock(), is_wishlist_actually_processing=lambda: False,
        reset_wishlist_processing_state=lambda: None,
        add_activity_item=lambda *a, **k: None,
        active_server=active_server, logger=logger)
    queued = 0
    token = set_library_scope(library_scope_for_profile(profile_id))
    try:
        for track in album.get("tracks") or []:
            payload, status = add_album_track_to_wishlist(
                runtime, track=track, artist=artist, album=album, source_type="album",
                source_context={"requested_via": requested_via})
            if status == 200 and payload.get("success") and not payload.get("skipped"):
                queued += 1
    finally:
        reset_library_scope(token)
    return queued
