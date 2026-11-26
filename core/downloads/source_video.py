"""a song that only exists on YouTube downloads from its own video (#2306).

a YouTube playlist's songs go through discovery like any other playlist's.
one that no catalogue knows (a cover, a live take, a channel's own track)
becomes a wing it stub with the video's own artist and title, and the
download then searched every source for it by name, often finding nothing,
or a different upload. the playlist already says which video it is, so the
download asks YouTube for that video first, and searches only when that
fails.

how the video is known: a stub from a YouTube playlist carries it
(``youtube_video_id``), and an older stub is found through the mirrored
playlist row that holds it. only a stub, and only a playlist from YouTube:
a song a catalogue identified downloads the usual way, from the best source.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.source_video")

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_SOURCES = ("youtube", "youtube_music", "ytmusic")


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            got = json.loads(value)
            return got if isinstance(got, dict) else {}
        except ValueError:
            return {}
    return {}


def valid_video_id(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if _VIDEO_ID.match(text) else None


def stub_id_of(track_info: Any) -> Optional[str]:
    from core.discovery.wing_it import is_stub_id
    info = _as_dict(track_info)
    for key in ("id", "spotify_track_id", "provider_track_id"):
        if is_stub_id(info.get(key)):
            return str(info[key])
    return None


def video_id_for(track_info: Any, db: Any = None) -> Optional[str]:
    """the YouTube video a stub download should take, or None"""
    info = _as_dict(track_info)
    stub = stub_id_of(info)
    if not stub:
        return None
    for holder in (info, _as_dict(info.get("source_info")), _as_dict(info.get("matched_data"))):
        vid = valid_video_id(holder.get("youtube_video_id"))
        if vid:
            return vid
    if db is None:
        return None
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                """
                SELECT t.source_track_id
                FROM mirrored_playlist_tracks t
                JOIN mirrored_playlists p ON p.id = t.playlist_id
                WHERE p.source IN ('youtube', 'youtube_music', 'ytmusic')
                  AND t.extra_data LIKE ?
                  AND json_extract(t.extra_data, '$.matched_data.id') = ?
                LIMIT 1
                """,
                (f"%{stub}%", stub),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - no table or no json: search as before
        logger.debug("source video lookup failed for %s: %s", stub, exc)
        return None
    return valid_video_id(row[0]) if row else None


def candidate_for(video_id: str, track_info: Any):
    """the video as a YouTube download candidate, in the shape a search
    result has, so the usual download path takes it"""
    from core.download_plugins.types import TrackResult
    info = _as_dict(track_info)
    title = str(info.get("name") or info.get("title") or "").strip()
    artists = info.get("artists") or []
    first = artists[0] if isinstance(artists, list) and artists else ""
    artist = first.get("name") if isinstance(first, dict) else str(first or "")
    duration = info.get("duration_ms") or None
    result = TrackResult(
        username="youtube", filename=f"{video_id}||{title}", size=0, bitrate=None,
        duration=int(duration) if duration else None, quality="", free_upload_slots=999,
        upload_speed=999999, queue_length=0, artist=artist or None, title=title or None,
        album=None, track_number=None)
    # the matcher's checks still run on it like any found file; this only
    # says where it came from
    result._source_metadata = {"source_video": True}
    return result


def youtube_client(deps: Any):
    """the YouTube download source, when it is set up, else None"""
    orch = getattr(deps, "download_orchestrator", None)
    if orch is None or not hasattr(orch, "client"):
        return None
    try:
        client = orch.client("youtube")
    except Exception:  # noqa: BLE001
        return None
    if client is None:
        return None
    try:
        if hasattr(client, "is_configured") and not client.is_configured():
            return None
    except Exception:  # noqa: BLE001
        return None
    return client
