"""Full-row snapshots for curated playlists - the hydration-fragility fix.

fresh tape and the archives used to store only track IDS and rehydrate
them from discovery_pool at read time. the pool rotates, so every
rotation silently shrank the playlists ("only 5-10 tracks" reports).
seasonal never had the problem because it carries its own table.

now curation ALSO stores the full rows under '<key>_full', and the read
path prefers them - the ids stay for compat with rows curated before
this shipped.
"""

import json
from typing import Any, Dict, List, Optional

FULL_SUFFIX = "_full"


def full_row_from_track_data(track_data: Dict[str, Any], source: str) -> Dict[str, Any]:
    """The endpoint-emission shape, built from the scanner's raw track dict
    (the fresh-tape path - it has full dicts in hand at curation time)."""
    artists = track_data.get("artists") or []
    artist_name = (artists[0].get("name", "Unknown")
                   if artists and isinstance(artists[0], dict) else "Unknown")
    album = track_data.get("album") or {}
    images = album.get("images") or []
    row = {
        "track_id": track_data.get("id"),
        "spotify_track_id": None,
        "itunes_track_id": None,
        "deezer_track_id": None,
        "track_name": track_data.get("name", "Unknown"),
        "artist_name": artist_name,
        "album_name": album.get("name", "Unknown"),
        "album_cover_url": (images[0].get("url") if images and isinstance(images[0], dict)
                            else None),
        "duration_ms": track_data.get("duration_ms", 0),
        "track_data_json": track_data,
        "source": source,
    }
    key = {"spotify": "spotify_track_id", "deezer": "deezer_track_id"}.get(
        source, "itunes_track_id")
    row[key] = track_data.get("id")
    return row


def full_row_from_pool_track(track: Any) -> Dict[str, Any]:
    """Same shape, from a discovery_pool row object (the archives path)."""
    track_data = getattr(track, "track_data_json", None)
    if isinstance(track_data, str):
        try:
            track_data = json.loads(track_data)
        except ValueError:
            track_data = None
    return {
        "track_id": (getattr(track, "spotify_track_id", None)
                     or getattr(track, "deezer_track_id", None)
                     or getattr(track, "itunes_track_id", None)),
        "spotify_track_id": getattr(track, "spotify_track_id", None),
        "itunes_track_id": getattr(track, "itunes_track_id", None),
        "deezer_track_id": getattr(track, "deezer_track_id", None),
        "track_name": getattr(track, "track_name", "Unknown"),
        "artist_name": getattr(track, "artist_name", "Unknown"),
        "album_name": getattr(track, "album_name", ""),
        "album_cover_url": getattr(track, "album_cover_url", None),
        "duration_ms": getattr(track, "duration_ms", 0),
        "track_data_json": track_data,
        "source": getattr(track, "source", ""),
    }


def read_curated_full(database, key: str, active_source: str,
                      profile_id: int = 1) -> Optional[List[Dict[str, Any]]]:
    """The read-side preference: '<key>_<source>_full' then '<key>_full'.
    None means no snapshot exists and the caller should fall back to the
    legacy id-hydration path."""
    for candidate in (f"{key}_{active_source}{FULL_SUFFIX}", f"{key}{FULL_SUFFIX}"):
        rows = database.get_curated_playlist(candidate, profile_id=profile_id)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows
    return None
