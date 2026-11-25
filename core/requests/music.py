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


def playlist_of(row: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """the playlist a row was asked for through, ``{id, name, source}``, or
    None. a playlist sync stamps its wishlist rows with the playlist's name
    (and id); nothing else does."""
    if str(row.get("source_type") or "").lower() != "playlist":
        return None
    info = row.get("source_info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except (ValueError, TypeError):
            info = {}
    if not isinstance(info, dict):
        return None
    name = str(info.get("playlist_name") or "").strip()
    pid = str(info.get("playlist_id") or "").strip()
    if not name and not pid:
        return None
    return {"id": pid, "name": name or "Playlist",
            "source": str(info.get("source") or info.get("playlist_source") or "").strip()}


def group_key(row: Dict[str, Any]) -> str:
    """what one ask is: the playlist a sync asked for it through, else the
    album a track came from, else the track alone.

    a playlist comes first (#2302): syncing a playlist with 31 missing songs
    made 31 unrelated requests (or one per album), and the admin could not
    tell which playlist had asked for any of them."""
    playlist = playlist_of(row)
    if playlist:
        return "playlist:" + (playlist["id"] or playlist["name"].lower())
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
            playlist = playlist_of(row) if key.startswith("playlist:") else None
            g = groups[key] = {
                "key": key,
                "profile_id": int(profile_id),
                "requester_name": requester_name,
                "kind": "playlist" if playlist else ("album" if is_album else "track"),
                "title": (playlist["name"] if playlist
                          else str(album.get("name") if is_album else data.get("name") or "Unknown")),
                # a playlist is many artists: its line says where it came from
                "artist": "" if playlist else _first_artist(data),
                "album": "" if playlist else str(album.get("name") or ""),
                "image_url": _image(data),
                "track_ids": [],
                "tracks": [],
                "created_at": row.get("date_added") or "",
            }
            if playlist:
                g["playlist"] = playlist
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


def narrow(group: Dict[str, Any], track_ids: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """the group cut down to some of its tracks, for answering part of a
    playlist's ask (approve these, decline those). None when none of the ids
    are in it. the key gains the tracks' own ids so its history row is not
    mistaken for the whole playlist's."""
    wanted = [str(t) for t in track_ids if str(t) in set(group.get("track_ids") or [])]
    if not wanted:
        return None
    if len(wanted) == len(group.get("track_ids") or []):
        return group
    tracks = [t for t in group.get("tracks") or [] if t.get("id") in wanted]
    out = dict(group)
    out.update({"track_ids": wanted, "tracks": tracks, "track_count": len(wanted),
                "part_of": group.get("track_count") or len(group.get("track_ids") or []),
                "key": f"{group['key']}#" + ",".join(sorted(wanted))[:120]})
    return out


def describe(group: Dict[str, Any]) -> str:
    """"In Rainbows by Radiohead" / "Reckoner by Radiohead" / "12 songs from Road Trip"."""
    if group.get("kind") == "playlist":
        n = len(group.get("track_ids") or [])
        return f"{n} song{'' if n == 1 else 's'} from {group.get('title') or 'a playlist'}"
    title = group.get("title") or "your request"
    artist = group.get("artist") or ""
    return f"{title} by {artist}" if artist else title


__all__ = ["group_key", "group_rows", "fulfillment_status", "describe", "GROUP_STATUSES",
           "playlist_of", "narrow"]
