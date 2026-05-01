"""Per-source metadata search.

Two public functions:

- `search_kind(client, query, kind, source_name=None)` — search a single
  result type (artists | albums | tracks) on one client and normalize the
  result to a list of plain dicts.

- `search_source(query, client, source_name=None)` — fan three
  search_kind calls out across a thread pool and return the merged dict.

Both swallow per-kind exceptions — search reliability matters more than
strict error propagation, and the route layer cannot do anything useful
with a single-kind failure.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

logger = logging.getLogger(__name__)


def search_kind(client, query: str, kind: str, source_name: Optional[str] = None) -> list:
    """Search one result type from a metadata source and normalize it."""
    source_label = source_name or type(client).__name__

    if kind == "artists":
        artists = []
        try:
            artist_objs = client.search_artists(query, limit=10)
            for artist in artist_objs:
                artists.append({
                    "id": artist.id,
                    "name": artist.name,
                    "image_url": artist.image_url,
                    "external_urls": artist.external_urls or {},
                })
        except Exception as e:
            logger.debug(f"Artist search failed for {source_label}: {e}")
        return artists

    if kind == "albums":
        albums = []
        try:
            album_objs = client.search_albums(query, limit=10)
            for album in album_objs:
                artist_name = ', '.join(album.artists) if album.artists else 'Unknown Artist'
                albums.append({
                    "id": album.id,
                    "name": album.name,
                    "artist": artist_name,
                    "image_url": album.image_url,
                    "release_date": album.release_date,
                    "total_tracks": album.total_tracks,
                    "album_type": album.album_type,
                    "external_urls": album.external_urls or {},
                })
        except Exception as e:
            logger.warning(f"Album search failed for {source_label}: {e}", exc_info=True)
        return albums

    if kind == "tracks":
        tracks = []
        try:
            track_objs = client.search_tracks(query, limit=10)
            for track in track_objs:
                artist_name = ', '.join(track.artists) if track.artists else 'Unknown Artist'
                tracks.append({
                    "id": track.id,
                    "name": track.name,
                    "artist": artist_name,
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "image_url": track.image_url,
                    "release_date": track.release_date,
                    "external_urls": track.external_urls or {},
                })
        except Exception as e:
            logger.warning(f"Track search failed for {source_label}: {e}", exc_info=True)
        return tracks

    raise ValueError(f"Unknown metadata search kind: {kind}")


def search_source(query: str, client, source_name: Optional[str] = None) -> dict:
    """Run all three search-kinds against a single client in parallel."""
    results: dict[str, Any] = {"artists": [], "albums": [], "tracks": []}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(search_kind, client, query, "artists", source_name): "artists",
            executor.submit(search_kind, client, query, "albums", source_name): "albums",
            executor.submit(search_kind, client, query, "tracks", source_name): "tracks",
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                results[kind] = future.result()
            except Exception as e:
                logger.warning(
                    f"{kind.title()} search failed for {source_name or type(client).__name__}: {e}",
                    exc_info=True,
                )
                results[kind] = []

    return {
        "artists": results["artists"],
        "albums": results["albums"],
        "tracks": results["tracks"],
        "available": True,
    }
