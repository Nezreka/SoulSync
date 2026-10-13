"""deezer 30 second previews as a stream source (#1312).

the stream search hands back a result like any other source, with
username PREVIEW_USERNAME and the deezer track id up front in filename.
/api/stream/start sees that and points the session straight at the clip:
no download, no Stream/ folder, nothing near the library. /stream/audio
proxies it like a navidrome stream, so the visualizer still gets
same-origin audio.

preview urls are signed and expire, so the one in the search result is
never used. the clip url is looked up again when playback starts.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("streaming.deezer_preview")

PREVIEW_USERNAME = "deezer_preview"
PREVIEW_SECONDS = 30


def is_preview_request(track_data: Optional[dict]) -> bool:
    return bool(track_data) and track_data.get("username") == PREVIEW_USERNAME


def preview_track_id(track_data: dict) -> Optional[str]:
    """the deezer id rides in front of '||' in filename, like the other
    streaming sources. a bare deezer_id key wins if a caller sent one."""
    raw = str(track_data.get("deezer_id") or "").strip()
    if not raw:
        raw = str(track_data.get("filename") or "").split("||", 1)[0].strip()
    return raw if raw.isdigit() else None


def find_preview(deezer_client, artist_name: str, track_title: str) -> Optional[dict]:
    """a stream-search result for the song's deezer preview, or None.

    search_track only answers with a hit by the artist we asked for and the
    version we named (no live cut or karaoke cover), which is the same bar
    the full stream search holds."""
    if not deezer_client or not artist_name or not track_title:
        return None
    try:
        hit = deezer_client.search_track(artist_name, track_title)
    except Exception as e:
        logger.warning("deezer preview search failed for %s - %s: %s", artist_name, track_title, e)
        return None
    if not hit or not hit.get("preview") or not hit.get("id"):
        return None
    title = hit.get("title") or track_title
    album = hit.get("album") or {}
    return {
        "username": PREVIEW_USERNAME,
        "filename": f"{hit['id']}||{title}",
        "deezer_id": str(hit["id"]),
        "title": title,
        "artist": (hit.get("artist") or {}).get("name") or artist_name,
        "album": album.get("title") or "",
        "image_url": album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium"),
        "size": 0,
        "bitrate": 128,
        "duration": PREVIEW_SECONDS,
        "quality": "Preview",
        "free_upload_slots": 1,
        "upload_speed": 0,
        "queue_length": 0,
        "result_type": "track",
        "preview": True,
    }


def session_state_for(track_data: dict, preview_url: str) -> dict[str, Any]:
    """what the stream session holds while a preview plays"""
    info = dict(track_data)
    info["is_preview"] = True
    return {
        "status": "ready",
        "progress": 100,
        "track_info": info,
        "file_path": None,
        "stream_url": preview_url,
        "error_message": None,
        "is_library": False,
    }
