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


def watchlist_facts(database, profile_id: int) -> Dict[str, Any]:
    """What the watched count is made of: who released something in the last
    30 days and how much, who was added this week, and the newest release."""
    facts: Dict[str, Any] = {"released_month": 0, "releases_month": 0,
                             "added_week": 0, "newest": None}
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            # a release dated in the future (an announced album) is not out yet
            out = "rr.release_date <= date('now', '+1 day')"
            cursor.execute(
                f"""SELECT COUNT(DISTINCT rr.watchlist_artist_id), COUNT(*)
                      FROM recent_releases rr
                      JOIN watchlist_artists wa ON wa.id = rr.watchlist_artist_id
                     WHERE wa.profile_id = ? AND {out}
                       AND rr.release_date >= date('now', '-30 days')""", (profile_id,))
            row = cursor.fetchone()
            facts["released_month"], facts["releases_month"] = int(row[0] or 0), int(row[1] or 0)
            cursor.execute(
                "SELECT COUNT(*) FROM watchlist_artists WHERE profile_id = ? "
                "AND date_added >= datetime('now', '-7 days')", (profile_id,))
            facts["added_week"] = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                f"""SELECT rr.album_name, wa.artist_name, rr.release_date, rr.album_cover_url
                      FROM recent_releases rr
                      JOIN watchlist_artists wa ON wa.id = rr.watchlist_artist_id
                     WHERE wa.profile_id = ? AND {out}
                     ORDER BY rr.release_date DESC, rr.added_date DESC LIMIT 1""", (profile_id,))
            newest = cursor.fetchone()
            if newest and newest[0]:
                facts["newest"] = {
                    "album": str(newest[0]), "artist": str(newest[1] or ""),
                    "date": str(newest[2] or "")[:10], "image_url": _loadable(newest[3]) or None}
    except Exception as exc:  # noqa: BLE001 - the count stands without its parts
        logger.debug("watchlist facts unavailable: %s", exc)
    return facts


def _first_artist(data: Dict[str, Any]) -> str:
    artists = data.get("artists") or []
    first = artists[0] if artists else None
    if isinstance(first, dict):
        return str(first.get("name") or "")
    return str(first or "")


def wishlist_facts(database, profile_id: int) -> Dict[str, Any]:
    """What the wished count is made of: how many releases and artists the
    tracks come from, how many are album tracks, singles or from an EP, and
    the newest wish."""
    facts: Dict[str, Any] = {"albums": 0, "artists": 0,
                             "kinds": {"album": 0, "single": 0, "ep": 0}, "newest": None}
    try:
        tracks = database.get_wishlist_tracks(profile_id=profile_id) or []
        albums, artists = set(), set()
        for track in tracks:
            data = track.get("spotify_data") if isinstance(track, dict) else None
            if not isinstance(data, dict):
                continue
            album = data.get("album") if isinstance(data.get("album"), dict) else {}
            kind = str(album.get("album_type") or "").strip().lower()
            # a compilation track is an album track; anything unnamed is a single,
            # the way the wishlist page files it
            kind = "album" if kind in ("album", "compilation") else "ep" if kind == "ep" else "single"
            facts["kinds"][kind] += 1
            artist = _first_artist(data)
            key = str(album.get("id") or album.get("name") or "").strip().casefold()
            if key:
                albums.add((key, artist.casefold()))
            if artist:
                artists.add(artist.casefold())
        facts["albums"], facts["artists"] = len(albums), len(artists)
        # the wishlist reads oldest first: the last one is the newest
        for track in reversed(tracks):
            data = track.get("spotify_data") if isinstance(track, dict) else None
            if isinstance(data, dict) and data.get("name"):
                facts["newest"] = {"name": str(data["name"]), "artist": _first_artist(data),
                                   "image_url": _sleeve(track) or None}
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("wishlist facts unavailable: %s", exc)
    return facts


__all__ = ["LIMIT", "watched_faces", "watchlist_facts", "wished_sleeves", "wishlist_facts"]
