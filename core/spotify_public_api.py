"""Full public-playlist fetch for the 'Spotify link' path, via the OPTIONAL
SpotipyFree library (no Spotify credentials needed).

Why a library: the embed scraper caps at ~100 tracks, and getting the full list
with no login means talking to Spotify's private API the way the web player
does — including client-auth headers Spotify rotates constantly. Rather than
chase those ourselves (we tried; Spotify 429s the bare token), we lean on
SpotipyFree — the maintained no-creds ``spotipy`` drop-in that spotDL uses,
which tracks those rotating bits for us.

Licensing: SpotipyFree is GPL-3.0, so it is NOT bundled or required by SoulSync
(MIT). It's an OPTIONAL install — if the user has run ``pip install spotipyFree``
this lights up; otherwise the import fails, this raises, and the caller
(``spotify_public_scraper.fetch_spotify_public``) falls back to the embed
scraper (today's ≤100). So SoulSync ships zero GPL code and stays cleanly MIT.

``client_factory`` is injectable so the orchestration is unit-testable without
the library or the network.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger('soulsync.spotify_public')

_MAX_TRACKS = 10000  # safety cap


def normalize_api_track(item: Any, index: int) -> Optional[Dict[str, Any]]:
    """Convert a spotipy-shape playlist item to the embed scraper's track shape.

    Returns None for items without a usable track id (local files, removed
    tracks, podcast episodes) so the caller can skip them.
    """
    track = (item or {}).get('track') or {}
    track_id = track.get('id')
    if not track_id:
        return None
    artists = [{'name': a.get('name', '')} for a in (track.get('artists') or []) if a.get('name')]
    return {
        'id': track_id,
        'name': track.get('name', 'Unknown Track'),
        'artists': artists or [{'name': 'Unknown Artist'}],
        'duration_ms': track.get('duration_ms', 0),
        'is_explicit': bool(track.get('explicit', False)),
        'track_number': index + 1,
    }


def _default_client():
    """Create a no-credentials SpotipyFree client.

    Raises ImportError when the optional GPL-3.0 library isn't installed — the
    caller treats that like any other failure and falls back to the embed
    scraper.
    """
    from SpotipyFree import Spotify  # optional, user-installed (GPL-3.0)
    return Spotify()


def fetch_public_playlist_full(
    spotify_id: str,
    *,
    client_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """Pull a public playlist's FULL track list with no credentials.

    Uses a SpotipyFree client (spotipy-compatible: ``playlist`` for metadata,
    ``playlist_items`` + ``next`` for paginated tracks). Returns the embed
    scraper's shape. Raises on any failure (incl. the library not being
    installed) so the caller can fall back to the embed scraper.
    """
    client = (client_factory or _default_client)()

    meta: Dict[str, Any] = {}
    try:
        # limit=1: we only want name/owner here — tracks come from the paginated
        # playlist_items call below, so don't pull the whole list twice.
        meta = client.playlist(spotify_id, limit=1) or {}
    except Exception as e:  # metadata is nice-to-have; tracks are the point
        logger.debug("playlist metadata fetch failed (%s); continuing", e)
    name = meta.get('name', 'Unknown')
    subtitle = (meta.get('owner') or {}).get('display_name', '')

    tracks: List[Dict[str, Any]] = []
    results = client.playlist_items(spotify_id)
    while results:
        for item in results.get('items', []):
            t = normalize_api_track(item, len(tracks))
            if t:
                tracks.append(t)
        if len(tracks) >= _MAX_TRACKS:
            break
        if results.get('next'):
            results = client.next(results)
        else:
            break

    if not tracks:
        raise RuntimeError('SpotipyFree returned no usable tracks')

    logger.info("SpotipyFree full fetch: %s (%d tracks)", name, len(tracks))
    source_url = f'https://open.spotify.com/playlist/{spotify_id}'
    return {
        'id': spotify_id,
        'type': 'playlist',
        'name': name,
        'subtitle': subtitle,
        'tracks': tracks,
        'url': source_url,
        'url_hash': hashlib.md5(source_url.encode()).hexdigest()[:12],
    }


__all__ = ['normalize_api_track', 'fetch_public_playlist_full']
