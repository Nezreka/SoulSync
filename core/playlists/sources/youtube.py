"""YouTube playlist source adapter.

Wraps ``parse_youtube_playlist`` (currently a free function in
``web_server.py`` — Phase 0 doesn't move it, just calls it via an
injected callable to avoid the circular import). ``supports_listing``
is False — YouTube playlists are URL-input only, no user library.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_YOUTUBE,
)


class YouTubePlaylistSource(PlaylistSource):
    name = SOURCE_YOUTUBE
    supports_listing = False
    supports_refresh = True
    requires_auth = False

    def __init__(self, parser: Callable[[str], Optional[dict]]):
        """``parser`` matches the signature of ``parse_youtube_playlist``
        in web_server.py — takes a URL, returns the playlist dict or
        ``None``. Injected so adapter can be constructed at import time."""
        self._parser = parser

    def is_authenticated(self) -> bool:
        return True

    def list_playlists(self) -> List[PlaylistMeta]:
        return []

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is the full YouTube playlist URL."""
        data = self._parser(playlist_id)
        if not data:
            return None

        source_url = data.get("url") or playlist_id
        url_hash = hashlib.md5(source_url.encode()).hexdigest()[:12]
        tracks_raw = data.get("tracks") or []

        meta = PlaylistMeta(
            source=self.name,
            source_playlist_id=url_hash,
            name=data.get("name", "YouTube Playlist"),
            track_count=int(data.get("track_count", len(tracks_raw))),
            image_url=data.get("image_url") or None,
            source_url=source_url,
            extra={
                "youtube_playlist_id": data.get("id"),
            },
        )

        tracks = [self._track_from_yt(t, idx) for idx, t in enumerate(tracks_raw) if t]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _track_from_yt(self, track: dict, position: int) -> NormalizedTrack:
        artists = track.get("artists") or []
        artist_name = artists[0] if artists else "Unknown Artist"
        return NormalizedTrack(
            position=position,
            track_name=track.get("name", "Unknown Track"),
            artist_name=artist_name,
            album_name=None,
            duration_ms=int(track.get("duration_ms", 0) or 0),
            source_track_id=str(track.get("id", "")),
            needs_discovery=False,
            extra={
                "url": track.get("url"),
                "raw_title": track.get("raw_title"),
                "raw_artist": track.get("raw_artist"),
            },
        )
