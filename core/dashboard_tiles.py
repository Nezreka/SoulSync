"""Pictures for the dashboard's two count tiles.

"58 artists watched" and "11 on your wishlist" were bare numbers. Each tile
now shows a few of what it counts: the newest watched artists' pictures, and
the sleeves of the songs most recently wished for. Read-only, a handful of
rows, and never a reason for the count to fail: every error is an empty list.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("dashboard_tiles")

LIMIT = 5


def _loadable(url: Any) -> str:
    """A picture a browser can load as it stands: a web address, or one of
    SoulSync's own image routes."""
    text = str(url or "").strip()
    return text if text.startswith(("http://", "https://", "/api/")) else ""


def watched_faces(database, profile_id: int, limit: int = LIMIT) -> List[Dict[str, str]]:
    """The newest watched artists that have a picture, newest first."""
    try:
        faces: List[Dict[str, str]] = []
        for artist in database.get_watchlist_artists(profile_id=profile_id):
            image = _loadable(getattr(artist, "image_url", None))
            if not image:
                continue
            faces.append({"name": str(getattr(artist, "artist_name", "") or ""), "image_url": image})
            if len(faces) >= limit:
                break
        return faces
    except Exception as exc:  # noqa: BLE001 - a picture is never worth the count
        logger.debug("watched faces unavailable: %s", exc)
        return []


def _sleeve(track: Any) -> str:
    data = track.get("spotify_data") if isinstance(track, dict) else None
    album = data.get("album") if isinstance(data, dict) else None
    if not isinstance(album, dict):
        return ""
    for image in album.get("images") or []:
        url = _loadable(image.get("url") if isinstance(image, dict) else image)
        if url:
            return url
    return _loadable(album.get("image_url"))


def wished_sleeves(database, profile_id: int, limit: int = LIMIT) -> List[Dict[str, str]]:
    """The covers of the songs most recently wished for, one per album."""
    try:
        tracks = database.get_wishlist_tracks(profile_id=profile_id) or []
        sleeves: List[Dict[str, str]] = []
        seen = set()
        # the wishlist reads oldest first (retry order): the tile shows the newest
        for track in reversed(tracks):
            url = _sleeve(track)
            if not url or url in seen:
                continue
            seen.add(url)
            data = track.get("spotify_data") or {}
            sleeves.append({"name": str(data.get("name") or ""), "image_url": url})
            if len(sleeves) >= limit:
                break
        return sleeves
    except Exception as exc:  # noqa: BLE001
        logger.debug("wished sleeves unavailable: %s", exc)
        return []


__all__ = ["LIMIT", "watched_faces", "wished_sleeves"]
