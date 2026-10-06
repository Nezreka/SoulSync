"""identify a mirrored track by its own id when the playlist and the
metadata source are the same service (#1566).

a mirrored deezer playlist already knows each track's deezer id. with deezer
as the metadata source, searching deezer by artist + title for that track can
only lose: the search can miss it or pick a reprise / karaoke version (#1565),
while /track/{id} is the exact track. same for spotify playlists with spotify
as the source.

returns the same Track object a search would, so the rest of identification
stores it unchanged. anything that doesn't pan out returns None and the
normal search runs, so this can never do worse than before.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.direct_match")

# mirror sources whose source_track_id is that service's own track id
_DEEZER_SOURCES = frozenset({"deezer"})
_SPOTIFY_SOURCES = frozenset({"spotify", "spotify_public"})


def direct_source_match(
    playlist_source: str,
    discovery_source: str,
    use_spotify: bool,
    source_track_id: Any,
    spotify_client: Any = None,
    fallback_client: Any = None,
) -> Optional[Any]:
    """the track itself, fetched by id, or None to fall back to the search."""
    track_id = str(source_track_id or "").strip()
    if not track_id:
        return None
    playlist_source = (playlist_source or "").lower()
    discovery_source = (discovery_source or "").lower()

    try:
        if (playlist_source in _DEEZER_SOURCES and discovery_source == "deezer"
                and not use_spotify):
            return _deezer_track(fallback_client, track_id)
        if (playlist_source in _SPOTIFY_SOURCES and discovery_source == "spotify"
                and use_spotify):
            return _spotify_track(spotify_client, track_id)
    except Exception as e:  # noqa: BLE001 - a failed lookup just means search
        logger.debug("direct lookup failed for %s track %s: %s",
                     playlist_source, track_id, e)
    return None


def _deezer_track(client: Any, track_id: str) -> Optional[Any]:
    from core.deezer_client import DeezerClient, Track as DeezerTrack

    # deezer track ids are numbers; anything else isn't one
    if not track_id.isdigit() or not isinstance(client, DeezerClient):
        return None
    raw = client.get_track_raw(int(track_id))
    if not raw or not raw.get("id"):
        return None
    return DeezerTrack.from_deezer_track(raw)


def _spotify_track(client: Any, track_id: str) -> Optional[Any]:
    from core.spotify_client import Track as SpotifyTrack

    if client is None:
        return None
    # allow_fallback=False: a spotify id must never be looked up in another
    # catalogue, where it could collide with a different track
    details = client.get_track_details(track_id, allow_fallback=False)
    raw = (details or {}).get("raw_data")
    if not raw or not raw.get("id"):
        return None
    return SpotifyTrack.from_spotify_track(raw)
