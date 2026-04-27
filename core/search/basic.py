"""Basic Soulseek file search — flat list of file results sorted by quality.

Used by the Soulseek source icon in the unified search UI and by direct
/api/search calls. Synchronous wrapper around the async soulseek client.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


def run_basic_soulseek_search(
    query: str,
    soulseek_client,
    run_async: Callable,
) -> list[dict]:
    """Search Soulseek for `query`, normalize albums + tracks to one sorted list.

    Returns dicts with `result_type` set to "album" or "track" and sorted by
    `quality_score` descending. Empty list on any failure (caller logs).
    """
    tracks, albums = run_async(soulseek_client.search(query))

    processed_albums = []
    for album in albums:
        album_dict = album.__dict__.copy()
        album_dict['tracks'] = [track.__dict__ for track in album.tracks]
        album_dict['result_type'] = 'album'
        processed_albums.append(album_dict)

    processed_tracks = []
    for track in tracks:
        track_dict = track.__dict__.copy()
        track_dict['result_type'] = 'track'
        processed_tracks.append(track_dict)

    return sorted(
        processed_albums + processed_tracks,
        key=lambda x: x.get('quality_score', 0),
        reverse=True,
    )
