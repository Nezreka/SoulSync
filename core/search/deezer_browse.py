"""The search page's browse tiles, from Deezer's public charts (#1984).

cremonies' idea: a tile like "Electronic & dance" should show what's charting
in that genre now, not only what SoulSync's metadata cache happens to know.
Deezer's chart and editorial endpoints are public (no account needed), and a
genre id works for both ``chart/<id>/...`` and ``editorial/<id>/releases``.

Each tile maps to one or more Deezer genres; their chart tracks and albums and
the new editorial releases are merged and deduped, newest-first for releases.
The rows come back in the shape the genre view already reads (the genre
deep-dive's), so search cards, previews and downloads work unchanged. A tile
Deezer has nothing for (ambient) answers None and the page keeps its cache.
Answers are kept for a few hours: charts move daily, not by the minute.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# the search page's tile ids -> Deezer genre ids (from GET /genre)
TILE_GENRES: Dict[str, Tuple[int, ...]] = {
    "charts": (0,),
    "new": (0,),
    "genre:electronic": (106, 113),      # Electro, Dance
    "genre:rock": (152, 85),             # Rock, Alternative
    "genre:hip hop": (116,),             # Rap/Hip Hop
    "genre:jazz": (129, 169),            # Jazz, Soul & Funk
    "genre:soundtrack": (173,),          # Films/Games
}

CACHE_SECONDS = 3 * 3600
_cache: Dict[str, Tuple[float, dict]] = {}
_lock = threading.Lock()


def _cover(item: dict) -> str:
    return str(item.get("cover_big") or item.get("cover_medium") or item.get("cover") or "")


def track_row(item: dict) -> Optional[dict]:
    """One chart track in the genre deep-dive's row shape."""
    if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
        return None
    album = item.get("album") or {}
    artist = item.get("artist") or {}
    return {
        "entity_id": str(item["id"]),
        "name": str(item["title"]),
        "artist_name": str(artist.get("name") or ""),
        "album_name": str(album.get("title") or ""),
        "album_id": str(album.get("id") or "") or None,
        "duration_ms": int(item.get("duration") or 0) * 1000 or None,
        "image_url": _cover(album),
        "source": "deezer",
        "popularity": item.get("rank"),
    }


def album_row(item: dict) -> Optional[dict]:
    """One chart album or editorial release in the deep-dive's row shape."""
    if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
        return None
    artist = item.get("artist") or {}
    return {
        "entity_id": str(item["id"]),
        "name": str(item["title"]),
        "artist_name": str(artist.get("name") or ""),
        "image_url": _cover(item),
        "release_date": str(item.get("release_date") or "") or None,
        "album_type": str(item.get("record_type") or "album"),
        "source": "deezer",
    }


def _merge(lists: Sequence[List[dict]], limit: int) -> List[dict]:
    """Interleave several charts, first appearance wins, so a two-genre tile
    shows both genres near the top instead of one after the other."""
    out: List[dict] = []
    seen = set()
    for i in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if i < len(rows):
                row = rows[i]
                key = row["entity_id"]
                if key not in seen:
                    seen.add(key)
                    out.append(row)
    return out[:limit]


def _data(client, endpoint: str, limit: int) -> List[dict]:
    payload = client._api_get(endpoint, {"limit": limit}, use_token=False) or {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def build_tile(client, tile: str, *, limit: int = 40) -> Optional[dict]:
    """{tracks, albums, artists: []} for one tile, or None when Deezer has no
    genre for it or answered nothing (the page then reads its cache)."""
    genres = TILE_GENRES.get(tile)
    if not genres or client is None:
        return None
    if tile == "new":
        releases = [r for r in map(album_row, _data(client, "editorial/0/releases", limit)) if r]
        releases.sort(key=lambda r: r.get("release_date") or "", reverse=True)
        tracks: List[dict] = []
        albums = releases[:limit]
    else:
        tracks = _merge([[r for r in map(track_row, _data(client, f"chart/{g}/tracks", limit)) if r]
                         for g in genres], limit)
        chart_albums = _merge([[r for r in map(album_row, _data(client, f"chart/{g}/albums", limit)) if r]
                               for g in genres], limit)
        if tile == "charts":
            albums = chart_albums
        else:
            fresh = _merge([[r for r in map(album_row, _data(client, f"editorial/{g}/releases", limit)) if r]
                            for g in genres], limit)
            albums = _merge([fresh, chart_albums], limit)
    if not tracks and not albums:
        return None
    return {"tracks": tracks, "albums": albums, "artists": [], "source": "deezer"}


def browse_tile(client, tile: str, *, now: Optional[float] = None) -> Optional[dict]:
    """build_tile, kept for CACHE_SECONDS per tile. An empty answer isn't kept,
    so Deezer coming back is picked up on the next visit."""
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(tile)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = build_tile(client, tile)
    if result is not None:
        with _lock:
            _cache[tile] = (now, result)
    return result


__all__ = ["TILE_GENRES", "build_tile", "browse_tile", "track_row", "album_row"]
