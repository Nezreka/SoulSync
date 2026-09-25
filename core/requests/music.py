"""music requests.

a profile without download rights still keeps a wishlist; the scheduled run
just doesn't download it. each row on it is a request waiting for an admin.
approving flips the rows to ``request_status = 'approved'``, which the
scheduled run DOES download, still on the requester's own wishlist, so they
watch it arrive. declining takes the rows off and ignore-lists them so a
watchlist scan doesn't put them straight back.

this module groups rows into what a person actually asked for (an album, or a
single track), and decides what an approved request looks like now. the db
and the library check come in as arguments so the rules test without either.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Optional

GROUP_STATUSES = ("pending", "approved", "available", "declined", "removed")


def _track_data(row: Dict[str, Any]) -> Dict[str, Any]:
    data = row.get("spotify_data") or row.get("track_data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            data = {}
    return data if isinstance(data, dict) else {}


def _first_artist(data: Dict[str, Any]) -> str:
    artists = data.get("artists") or []
    if isinstance(artists, list) and artists:
        a = artists[0]
        return str(a.get("name") if isinstance(a, dict) else a or "")
    return str(data.get("artist") or "")


def _image(data: Dict[str, Any]) -> str:
    album = data.get("album") if isinstance(data.get("album"), dict) else {}
    images = album.get("images") or data.get("images") or []
    if isinstance(images, list) and images:
        first = images[0]
        return str(first.get("url") if isinstance(first, dict) else first or "")
    return str(album.get("image_url") or data.get("image_url") or "")


def group_key(row: Dict[str, Any]) -> str:
    """what one ask is: the album a track came from, else the track alone."""
    data = _track_data(row)
    album = data.get("album") if isinstance(data.get("album"), dict) else {}
    album_id = album.get("id")
    album_type = str(album.get("album_type") or "").lower()
    source_type = str(row.get("source_type") or "").lower()
    if album_id and (album_type in ("album", "compilation", "ep") or source_type == "album"):
        return f"album:{album_id}"
    if album.get("name") and source_type == "album":
        return f"album:{_first_artist(data).lower()}|{str(album['name']).lower()}"
    return f"track:{row.get('spotify_track_id') or data.get('id')}"


def group_rows(rows: Iterable[Dict[str, Any]], profile_id: int, requester_name: str = "") -> List[Dict[str, Any]]:
    """wishlist rows of one profile -> request groups, newest ask first."""
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        data = _track_data(row)
        key = group_key(row)
        album = data.get("album") if isinstance(data.get("album"), dict) else {}
        g = groups.get(key)
        if g is None:
            is_album = key.startswith("album:")
            g = groups[key] = {
                "key": key,
                "profile_id": int(profile_id),
                "requester_name": requester_name,
                "kind": "album" if is_album else "track",
                "title": str(album.get("name") if is_album else data.get("name") or "Unknown"),
                "artist": _first_artist(data),
                "album": str(album.get("name") or ""),
                "image_url": _image(data),
                "track_ids": [],
                "tracks": [],
                "created_at": row.get("date_added") or "",
            }
        tid = str(row.get("spotify_track_id") or data.get("id") or "")
        if tid and tid not in g["track_ids"]:
            g["track_ids"].append(tid)
            g["tracks"].append({"id": tid, "title": str(data.get("name") or ""),
                                "artist": _first_artist(data)})
        created = row.get("date_added") or ""
        if created and (not g["created_at"] or str(created) > str(g["created_at"])):
            g["created_at"] = created
    out = list(groups.values())
    for g in out:
        g["track_count"] = len(g["track_ids"])
    out.sort(key=lambda g: str(g["created_at"]), reverse=True)
    return out


def fulfillment_status(tracks: List[Dict[str, Any]], still_wishlisted: Callable[[str], bool],
                       in_library: Callable[[str, str], bool]) -> Optional[str]:
    """where an approved request stands. None = still on its way (some rows
    are still on the wishlist). once every row has left the wishlist, it's
    'available' when the library has them, 'removed' when someone took them
    off without them arriving."""
    if not tracks:
        return "removed"
    if any(still_wishlisted(t.get("id") or "") for t in tracks):
        return None
    found = sum(1 for t in tracks if in_library(t.get("title") or "", t.get("artist") or ""))
    return "available" if found else "removed"


def describe(group: Dict[str, Any]) -> str:
    """"In Rainbows by Radiohead" / "Reckoner by Radiohead"."""
    title = group.get("title") or "your request"
    artist = group.get("artist") or ""
    return f"{title} by {artist}" if artist else title


__all__ = ["group_key", "group_rows", "fulfillment_status", "describe", "GROUP_STATUSES"]
