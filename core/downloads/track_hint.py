"""the song a download search is for, for sources that can use it.

download searches hand every source a plain query string. that's all
soulseek can use, but a catalog source can do better with the song itself:
deezer's free-text search puts the reprise, karaoke and key-shifted copies
of "How Far I'll Go" first and leaves the original out entirely (#1582),
while a field-scoped title search or the track's own deezer id finds it.

the hint rides a ContextVar set around the orchestrator's search, the same
way the quality profile does, so a source opts in by reading it and every
other source is untouched. run_blocking copies context into its thread.
"""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from typing import Any, Dict, Optional

_ACTIVE_TRACK_HINT: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "download_track_hint", default=None)

_DEEZER_LINK_ID = re.compile(r"deezer\.com/(?:[a-z]{2}/)?track/(\d+)")


@contextmanager
def track_hint_context(hint: Optional[Dict[str, Any]] = None):
    token = _ACTIVE_TRACK_HINT.set(hint or None)
    try:
        yield
    finally:
        _ACTIVE_TRACK_HINT.reset(token)


def current_track_hint() -> Optional[Dict[str, Any]]:
    return _ACTIVE_TRACK_HINT.get()


def _artist_name(artist: Any) -> str:
    if isinstance(artist, dict):
        return str(artist.get("name") or "").strip()
    return str(artist or "").strip()


def deezer_track_id(track_data: Dict[str, Any]) -> Optional[str]:
    """the deezer id of a track that came from deezer, else None. only trusted
    when the track says it's deezer's: a bare numeric id could be itunes"""
    if not isinstance(track_data, dict):
        return None
    uri = str(track_data.get("uri") or "")
    if uri.startswith("deezer:track:"):
        tid = uri.rsplit(":", 1)[-1]
        return tid if tid.isdigit() else None
    urls = track_data.get("external_urls")
    link = urls.get("deezer") if isinstance(urls, dict) else None
    m = _DEEZER_LINK_ID.search(str(link or ""))
    if m:
        return m.group(1)
    if str(track_data.get("_source") or track_data.get("source") or "").lower() == "deezer":
        tid = str(track_data.get("id") or "")
        return tid if tid.isdigit() else None
    return None


def hint_from_track(track_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{title, artist, deezer_id} for a download task's track. the artist is
    the track's own first artist, not the album artist: a soundtrack's album
    artist ("Alan Menken, Aladdin - Cast, Disney") names nobody on the song"""
    if not isinstance(track_data, dict):
        return None
    title = str(track_data.get("name") or track_data.get("title") or "").strip()
    if not title:
        return None
    artists = track_data.get("artists") or []
    artist = _artist_name(artists[0]) if isinstance(artists, list) and artists else _artist_name(
        track_data.get("artist"))
    return {"title": title, "artist": artist, "deezer_id": deezer_track_id(track_data)}


__all__ = ["track_hint_context", "current_track_hint", "hint_from_track", "deezer_track_id"]
