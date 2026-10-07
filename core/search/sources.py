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


def search_kind(client, query: str, kind: str, source_name: Optional[str] = None,
                prefer_free: bool = False) -> list:
    """Search one result type from a metadata source and normalize it.

    Provider errors return ``[]`` — preserved behavior for direct callers.
    (Logging happens inside the raw worker before it re-raises.)
    """
    try:
        return _search_kind_raw(client, query, kind, source_name=source_name, prefer_free=prefer_free)
    except ValueError:
        raise
    except Exception:
        return []


def _search_kind_raw(client, query: str, kind: str, source_name: Optional[str] = None,
                   prefer_free: bool = False) -> list:
    """Search one result type from a metadata source and normalize it.

    Identical to :func:`search_kind` except provider exceptions propagate
    instead of returning ``[]`` — used by :func:`search_source` so it can
    count failed kinds and report whether the source is actually available.
    """
    source_label = source_name or type(client).__name__

    # prefer_free is only ever set for an explicit Spotify pick (see the
    # orchestrator), so only the SpotifyClient — which accepts the kwarg — ever
    # receives it. Passing it only when True keeps every other client's signature
    # untouched.
    extra = {"prefer_free": True} if prefer_free else {}

    if kind == "artists":
        artists = []
        try:
            artist_objs = client.search_artists(query, limit=10, **extra)
            for artist in artist_objs:
                artists.append({
                    "id": artist.id,
                    "name": artist.name,
                    "source": source_name or "",
                    "image_url": artist.image_url,
                    "external_urls": artist.external_urls or {},
                    # the search page tells same-named artists apart by it
                    "followers": getattr(artist, "followers", None) or None,
                })
        except Exception as e:
            logger.debug(f"Artist search failed for {source_label}: {e}")
            raise
        return artists

    if kind == "albums":
        albums = []
        try:
            album_objs = client.search_albums(query, limit=10, **extra)
            for album in album_objs:
                artist_name = ', '.join(album.artists) if album.artists else 'Unknown Artist'
                albums.append({
                    "id": album.id,
                    "name": album.name,
                    "artist": artist_name,
                    "source": source_name or "",
                    "image_url": album.image_url,
                    "release_date": album.release_date,
                    "total_tracks": album.total_tracks,
                    "album_type": album.album_type,
                    "format": getattr(album, "format", None),
                    "country": getattr(album, "country", None),
                    "status": getattr(album, "status", None),
                    "label": getattr(album, "label", None),
                    "disambiguation": getattr(album, "disambiguation", None),
                    "release_group_id": getattr(album, "release_group_id", None),
                    "external_urls": album.external_urls or {},
                    # kids profiles filter on it; None = the source didn't say
                    "explicit": getattr(album, "explicit", None),
                })
        except Exception as e:
            logger.warning(f"Album search failed for {source_label}: {e}", exc_info=True)
            raise
        return albums

    if kind == "tracks":
        tracks = []
        try:
            # Deezer's free text can leave the real song out of the page
            # entirely; for a query that names an artist this also runs the
            # exact-title search (other sources: unchanged).
            from core.metadata.song_search import search_typed_query
            track_objs = search_typed_query(client, query, limit=10, **extra)
            for track in track_objs:
                artist_name = ', '.join(track.artists) if track.artists else 'Unknown Artist'
                tracks.append({
                    "id": track.id,
                    "name": track.name,
                    "artist": artist_name,
                    # The REAL artist list, not the joined display string above.
                    # Spotify/Tidal/iTunes searches return collabs as a list;
                    # collapsing them to one "A, B" string made the import
                    # pipeline tag downloads with a single combined artist
                    # (resolve_track_artists saw one value). The frontend keeps
                    # using "artist" for display.
                    "artists": list(track.artists or []),
                    # Which metadata source this result came from. Travels with
                    # the payload through Download Now -> download task ->
                    # import context, where extract_source_metadata needs it to
                    # run source-specific logic (the Deezer contributors
                    # upgrade for multi-artist tags — Netti93's report: without
                    # it get_import_source() resolved '' and collab tracks
                    # were tagged with only the primary artist until a Retag).
                    "source": source_name or "",
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "image_url": track.image_url,
                    "release_date": track.release_date,
                    "external_urls": track.external_urls or {},
                    "explicit": getattr(track, "explicit", None),
                })
        except Exception as e:
            logger.warning(f"Track search failed for {source_label}: {e}", exc_info=True)
            raise
        return tracks

    if kind == "playlists":
        playlists = []
        try:
            if hasattr(client, "search_playlists"):
                playlist_objs = client.search_playlists(query, limit=10)
                for playlist in (playlist_objs or []):
                    if isinstance(playlist, dict):
                        playlists.append({
                            "id": str(playlist.get("id") or ""),
                            "name": playlist.get("title") or playlist.get("name") or "",
                            "creator": playlist.get("creator") or (playlist.get("owner") or {}).get("display_name", "") or "",
                            "track_count": int(playlist.get("track_count") or playlist.get("nb_tracks") or 0),
                            "image_url": playlist.get("image_url") or "",
                            "source": source_name or playlist.get("source") or "",
                            "link": playlist.get("link") or "",
                        })
                    else:
                        playlists.append({
                            "id": str(getattr(playlist, "id", "") or ""),
                            "name": getattr(playlist, "name", "") or getattr(playlist, "title", "") or "",
                            "creator": getattr(playlist, "creator", "") or "",
                            "track_count": int(getattr(playlist, "track_count", 0) or 0),
                            "image_url": getattr(playlist, "image_url", "") or "",
                            "source": source_name or getattr(playlist, "source", "") or "",
                            "link": getattr(playlist, "link", "") or "",
                        })
            elif (hasattr(client, "sp") and client.sp
                  and hasattr(client, "is_spotify_authenticated")
                  and client.is_spotify_authenticated()):
                results = client.sp.search(q=query, type='playlist', limit=10)
                items = ((results or {}).get('playlists') or {}).get('items') or []
                for p in items:
                    if not p:
                        continue
                    images = p.get('images') or []
                    image_url = images[0].get('url', '') if images else ''
                    owner = p.get('owner') or {}
                    playlists.append({
                        "id": str(p.get("id") or ""),
                        "name": p.get("name") or "",
                        "creator": owner.get("display_name") or "Spotify",
                        "track_count": int((p.get("tracks") or {}).get("total") or 0),
                        "image_url": image_url,
                        "source": source_name or "spotify",
                        "link": (p.get("external_urls") or {}).get("spotify", ""),
                    })
        except Exception as e:
            logger.debug(f"Playlist search failed for {source_label}: {e}")
            raise
        return playlists

    raise ValueError(f"Unknown metadata search kind: {kind}")


def search_source(query: str, client, source_name: Optional[str] = None,
                  prefer_free: bool = False) -> dict:
    """Run search-kinds against a single client in parallel.

    ``available`` is True when at least one kind succeeded; False only when
    every kind failed — so a provider outage is distinguishable from a
    healthy empty result (M16).
    """
    results: dict[str, Any] = {"artists": [], "albums": [], "tracks": [], "playlists": []}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_search_kind_raw, client, query, "artists", source_name, prefer_free): "artists",
            executor.submit(_search_kind_raw, client, query, "albums", source_name, prefer_free): "albums",
            executor.submit(_search_kind_raw, client, query, "tracks", source_name, prefer_free): "tracks",
            executor.submit(_search_kind_raw, client, query, "playlists", source_name, prefer_free): "playlists",
        }
        failed_kinds = 0
        for future in as_completed(futures):
            kind = futures[future]
            try:
                results[kind] = future.result()
            except Exception as e:
                failed_kinds += 1
                logger.warning(
                    f"{kind.title()} search failed for {source_name or type(client).__name__}: {e}",
                    exc_info=True,
                )
                results[kind] = []

    return {
        "artists": results["artists"],
        "albums": results["albums"],
        "tracks": results["tracks"],
        "playlists": results["playlists"],
        "available": failed_kinds < len(futures),
    }
