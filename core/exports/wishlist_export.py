"""Export a wishlist, music or video, to JSON or CSV (c5pie's request).

Pure shaping + formatting. The routes read the wishlist the same way the page
does (so an export lists exactly what the page lists) and hand the rows here.
One flat row per wanted item: a CSV opens in a spreadsheet as is, and the
JSON is the same rows as objects.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, Iterable, List

VALID_FORMATS = ("json", "csv")

MUSIC_FIELDS = ["title", "artist", "album", "album_type", "release_date", "track_number",
                "duration", "isrc", "spotify_id", "added", "from"]
VIDEO_FIELDS = ["type", "title", "year", "season", "episode", "episode_title", "air_date",
                "tmdb_id", "status", "added"]

# what each video wishlist kind is called in an export, and which filter takes it
_VIDEO_TYPES = {"movie": "movie", "episode": "tv", "video": "youtube"}
VIDEO_SCOPES = {"all": None, "movie": ("movie",), "show": ("episode",)}


def _obj(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _names(artists: Any) -> str:
    out = []
    for a in artists if isinstance(artists, list) else []:
        name = a.get("name") if isinstance(a, dict) else a
        if name:
            out.append(str(name))
    return ", ".join(out)


def _duration(ms: Any) -> str:
    try:
        seconds = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds > 0 else ""


def music_rows(tracks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per wanted track, from the rows the wishlist page lists."""
    rows = []
    for track in tracks or []:
        data = _obj(track.get("spotify_data")) or track
        album = data.get("album") if isinstance(data.get("album"), dict) else {}
        source = _obj(track.get("source_info"))
        external = data.get("external_ids") if isinstance(data.get("external_ids"), dict) else {}
        rows.append({
            "title": data.get("name") or track.get("name") or "",
            "artist": _names(data.get("artists")) or str(data.get("artist") or ""),
            "album": album.get("name") or (data.get("album") if isinstance(data.get("album"), str) else ""),
            "album_type": album.get("album_type") or "",
            "release_date": album.get("release_date") or "",
            "track_number": data.get("track_number") or "",
            "duration": _duration(data.get("duration_ms")),
            "isrc": external.get("isrc") or data.get("isrc") or "",
            "spotify_id": track.get("spotify_track_id") or data.get("id") or "",
            "added": track.get("date_added") or "",
            # the playlist that wanted it, else what kind of add it was
            "from": source.get("playlist_name") or track.get("source_type") or "",
        })
    return rows


def video_rows(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per wanted movie, episode or YouTube video."""
    rows = []
    for item in items or []:
        kind = str(item.get("kind") or "")
        rows.append({
            "type": _VIDEO_TYPES.get(kind, kind),
            "title": item.get("title") or "",
            "year": item.get("year") or (str(item.get("release_date") or "")[:4]),
            "season": item.get("season_number") if item.get("season_number") is not None else "",
            "episode": item.get("episode_number") if item.get("episode_number") is not None else "",
            "episode_title": item.get("episode_title") or "",
            "air_date": item.get("air_date") or "",
            "tmdb_id": item.get("tmdb_id") or "",
            "status": item.get("status") or "",
            "added": item.get("date_added") or "",
        })
    return rows


def render(rows: List[Dict[str, Any]], fields: List[str], fmt: str = "json") -> str:
    """``rows`` as a JSON list of objects, or a CSV with a header row."""
    if (fmt or "json").lower() == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()
    return json.dumps([{k: row.get(k, "") for k in fields} for row in rows],
                      indent=2, ensure_ascii=False)


__all__ = ["MUSIC_FIELDS", "VIDEO_FIELDS", "VIDEO_SCOPES", "VALID_FORMATS",
           "music_rows", "render", "video_rows"]
