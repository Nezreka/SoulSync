"""iTunes / Apple Music link playlist source adapter.

Wraps the iTunes-link parsing logic that currently lives in
``web_server.py`` (commit 718eb0cb). Phase 0 doesn't move it — adapter
takes a parser callable that returns the parsed-playlist dict matching
the ``parse_itunes_link_endpoint`` response shape::

    {
        'id', 'type', 'name', 'subtitle', 'url', 'url_hash',
        'track_count', 'image_url',
        'tracks': [{ 'id', 'name', 'artists', 'album', 'duration_ms', ... }],
    }

``supports_listing=False`` — Apple Music has no "user library" listing,
the user pastes a URL.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_ITUNES_LINK,
)


class ITunesLinkPlaylistSource(PlaylistSource):
    name = SOURCE_ITUNES_LINK
    supports_listing = False
    supports_refresh = True
    requires_auth = False

    def __init__(self, parser: Callable[[str], Optional[dict]]):
        """``parser(url)`` returns the parsed playlist dict, or ``None``
        if the URL is invalid / unreachable. Injected by ``web_server.py``
        at startup, pointing at the existing module-level helpers."""
        self._parser = parser

    def is_authenticated(self) -> bool:
        return True

    def list_playlists(self) -> List[PlaylistMeta]:
        return []

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is the full Apple Music / iTunes URL."""
        data = self._parser(playlist_id)
        if not data:
            return None

        source_url = data.get("url") or playlist_id
        tracks_raw = data.get("tracks") or []

        meta = PlaylistMeta(
            source=self.name,
            source_playlist_id=str(data.get("url_hash") or data.get("id") or ""),
            name=data.get("name", "Apple Music Link"),
            owner=data.get("subtitle"),
            image_url=data.get("image_url") or None,
            track_count=int(data.get("track_count", len(tracks_raw))),
            source_url=source_url,
            extra={
                "itunes_type": data.get("type"),
                "itunes_id": data.get("id"),
            },
        )

        tracks = [self._track_from_itunes(t, idx) for idx, t in enumerate(tracks_raw) if t]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _track_from_itunes(self, track: dict, position: int) -> NormalizedTrack:
        artists = track.get("artists") or []
        if artists and isinstance(artists[0], dict):
            artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name"))
        else:
            artist_name = ", ".join(str(a) for a in artists if a)
        if not artist_name:
            artist_name = "Unknown Artist"
        album = track.get("album")
        album_name: Optional[str] = None
        if isinstance(album, dict):
            album_name = album.get("name") or None
        elif album:
            album_name = str(album)
        return NormalizedTrack(
            position=position,
            track_name=track.get("name", "Unknown Track"),
            artist_name=artist_name,
            album_name=album_name,
            duration_ms=int(track.get("duration_ms", 0) or 0),
            source_track_id=str(track.get("id", "")),
            image_url=track.get("image_url"),
            needs_discovery=False,
            extra={
                "external_urls": track.get("external_urls"),
                "preview_url": track.get("preview_url"),
            },
        )
