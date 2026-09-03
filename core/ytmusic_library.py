"""Read the signed-in YouTube Music account's own library via ytmusicapi.

Companion to ``core.youtube_music_meta``, which reads a SINGLE playlist by
URL (anonymous or authenticated). This module answers a different
question — "what playlists does THIS account have?" — which is what
``YouTubePlaylistSource.supports_listing = False`` cannot do. See
``core.playlists.sources.ytmusic.YTMusicPlaylistSource``, the adapter that
consumes it.

Same house rules as ``youtube_music_meta``: ``ytmusicapi`` is optional at
runtime, every function is best-effort and returns ``None`` (never raises)
on any failure, and ``auth`` is a ytmusicapi browser-auth header dict — see
``core.youtube_cookies.ytmusic_auth_headers``.

Two calls, not one, for Liked Music: ``get_library_playlists()`` is
unreliable for it — some accounts get an "Auto playlist" row for it in the
regular library grid (``playlistId: 'LM'``) with no usable ``count``, others
get nothing at all (verified against ytmusicapi 1.12's ``parse_playlist`` —
that grid isn't the same shelf ``get_liked_songs()`` reads). Either way it
needs its own lookup by the reserved id ``"LM"`` (``LIKED_MUSIC_ID``,
re-exported from ``youtube_music_meta``), the same one
``ytmusicapi.YTMusic.get_liked_songs()`` uses internally. ``get_playlist(id,
limit=0)`` fetches the header (title, track count, thumbnail) without
paging any tracks — the same cheap-count trick
``TidalClient.get_collection_tracks_count()`` uses for Favorite Tracks.
``library_playlist_to_row`` drops any ``LIKED_MUSIC_ID`` row it sees in the
regular grid, so the two calls can never produce two "Liked Music" entries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.youtube_music_meta import LIKED_MUSIC_ID
from utils.logging_config import get_logger

logger = get_logger("ytmusic_library")

LIKED_MUSIC_NAME = "Liked Music"


def _thumbnail_url(entry: Mapping[str, Any]) -> str:
    thumbnails = entry.get("thumbnails") or []
    if thumbnails and isinstance(thumbnails[-1], Mapping):
        return str(thumbnails[-1].get("url") or "")
    return ""


def _owner_name(author: Any) -> Optional[str]:
    """``author`` shape varies: absent, a plain string, a single dict, or a
    list of ``{name, id}`` dicts (ytmusicapi's ``parse_artists_runs``)."""
    if isinstance(author, list) and author:
        first = author[0]
        name = first.get("name") if isinstance(first, Mapping) else str(first)
        return (name or "").strip() or None
    if isinstance(author, Mapping):
        return (str(author.get("name") or "")).strip() or None
    if isinstance(author, str) and author.strip():
        return author.strip()
    return None


def library_playlist_to_row(entry: Any) -> Optional[Dict[str, Any]]:
    """Project one ``get_library_playlists()`` row, or ``None``. Pure.

    Requires a ``playlistId`` and a ``title`` — a playlist that can't be
    identified or shown is not a usable row. Also drops a ``LIKED_MUSIC_ID``
    row outright: some accounts surface an "Auto playlist" entry for Liked
    Music in this same grid with no usable ``count``, and
    ``fetch_liked_music_row`` already builds the correct row for it from its
    own dedicated header lookup. Letting both through gives two "Liked Music"
    rows sharing one id, and whichever the frontend renders second silently
    wins."""
    if not isinstance(entry, Mapping):
        return None
    playlist_id = str(entry.get("playlistId") or "").strip()
    title = str(entry.get("title") or "").strip()
    if not playlist_id or not title:
        return None
    if playlist_id == LIKED_MUSIC_ID:
        return None

    count_raw = entry.get("count")
    try:
        track_count = int(count_raw) if count_raw not in (None, "") else 0
    except (TypeError, ValueError):
        track_count = 0

    return {
        "id": playlist_id,
        "name": title,
        "track_count": track_count,
        "image_url": _thumbnail_url(entry) or None,
        "description": (str(entry.get("description") or "").strip() or None),
        "owner": _owner_name(entry.get("author")),
    }


def library_playlists_to_rows(raw: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Project every ``get_library_playlists()`` row. Pure. ``raw=None`` -> ``[]``."""
    if not raw:
        return []
    rows: List[Dict[str, Any]] = []
    for entry in raw:
        row = library_playlist_to_row(entry)
        if row:
            rows.append(row)
    return rows


def fetch_library_playlists(auth: Optional[Dict[str, str]]) -> Optional[List[Any]]:
    """Fetch the signed-in account's own library playlists, or ``None`` on any failure.

    ``auth`` is required — the library has no anonymous view, so a missing
    ``auth`` short-circuits before the (guaranteed to fail) network call.

    Never raises: missing ytmusicapi, a network blip, an expired cookie jar
    and an upstream response-shape change all mean the same thing to the
    caller — treat the account as unreachable right now.
    """
    if not auth:
        return None
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        logger.debug("ytmusicapi not installed — cannot list library playlists")
        return None

    try:
        client = YTMusic(auth)
        return client.get_library_playlists(limit=None)
    except Exception as e:  # noqa: BLE001 - see docstring: all failures return None
        logger.info(
            "YouTube Music library playlists fetch failed (%s: %s)",
            type(e).__name__, e,
        )
        return None


def fetch_liked_music_row(auth: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Fetch a listing row for the account's Liked Music, or ``None``.

    ``None`` covers both "the call failed" and "there is nothing liked yet"
    (``track_count <= 0``) — callers that want a virtual playlist card treat
    both the same way Tidal's Favorite Tracks entry does: don't show an
    empty ghost row.
    """
    if not auth:
        return None
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        logger.debug("ytmusicapi not installed — cannot fetch Liked Music")
        return None

    try:
        client = YTMusic(auth)
        header = client.get_playlist(LIKED_MUSIC_ID, limit=0)
    except Exception as e:  # noqa: BLE001 - see docstring: all failures return None
        logger.info(
            "YouTube Music Liked Music header fetch failed (%s: %s)",
            type(e).__name__, e,
        )
        return None

    if not isinstance(header, Mapping):
        return None
    track_count_raw = header.get("trackCount")
    try:
        track_count = int(track_count_raw) if track_count_raw is not None else 0
    except (TypeError, ValueError):
        track_count = 0
    if track_count <= 0:
        return None

    return {
        "id": LIKED_MUSIC_ID,
        "name": str(header.get("title") or LIKED_MUSIC_NAME),
        "track_count": track_count,
        "image_url": _thumbnail_url(header) or None,
        "description": None,
        "owner": "You",
    }


def ytmusic_playlist_url(playlist_id: str) -> str:
    """The ``music.youtube.com`` URL form ``fetch_ytmusic_playlist`` /
    ``parse_youtube_playlist`` expect."""
    return f"https://music.youtube.com/playlist?list={playlist_id}"


__all__ = [
    "LIKED_MUSIC_NAME",
    "library_playlist_to_row",
    "library_playlists_to_rows",
    "fetch_library_playlists",
    "fetch_liked_music_row",
    "ytmusic_playlist_url",
]
