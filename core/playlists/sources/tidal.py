"""Tidal playlist source adapter.

Wraps ``core.tidal_client.TidalClient``. Tidal recognizes the virtual
``tidal-favorites`` ID inside ``get_playlist`` already, so the adapter
doesn't need to special-case it.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_TIDAL,
)


class TidalPlaylistSource(PlaylistSource):
    name = SOURCE_TIDAL
    supports_listing = True
    supports_refresh = True
    requires_auth = True

    def __init__(self, client_getter: Callable[[], Any]):
        self._client_getter = client_getter

    def _client(self):
        return self._client_getter()

    def is_authenticated(self) -> bool:
        client = self._client()
        if client is None:
            return False
        return bool(client.is_authenticated())

    def list_playlists(self) -> List[PlaylistMeta]:
        client = self._client()
        if client is None or not client.is_authenticated():
            return []
        playlists = client.get_user_playlists_metadata_only() or []
        return [self._meta_from_playlist(p) for p in playlists]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        client = self._client()
        if client is None or not client.is_authenticated():
            return None
        playlist = client.get_playlist(playlist_id)
        if playlist is None:
            return None
        meta = self._meta_from_playlist(playlist)
        tracks_raw = getattr(playlist, "tracks", None) or []
        tracks = [self._track_from_tidal(t, idx) for idx, t in enumerate(tracks_raw)]
        meta.track_count = len(tracks)
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_playlist(self, playlist: Any) -> PlaylistMeta:
        owner_field = getattr(playlist, "owner", None)
        owner_name: Optional[str] = None
        if isinstance(owner_field, dict):
            owner_name = owner_field.get("name") or owner_field.get("id")
        elif owner_field:
            owner_name = str(owner_field)
        tracks_raw = getattr(playlist, "tracks", None) or []
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(playlist.id),
            name=playlist.name,
            owner=owner_name,
            description=getattr(playlist, "description", "") or None,
            track_count=len(tracks_raw),
            extra={
                "public": bool(getattr(playlist, "public", True)),
                "external_urls": getattr(playlist, "external_urls", {}),
            },
        )

    def _track_from_tidal(self, track: Any, position: int) -> NormalizedTrack:
        artists = getattr(track, "artists", None) or []
        artist_name = ", ".join(artists) if artists else "Unknown Artist"
        return NormalizedTrack(
            position=position,
            track_name=track.name,
            artist_name=artist_name,
            album_name=getattr(track, "album", "") or None,
            duration_ms=int(getattr(track, "duration_ms", 0) or 0),
            source_track_id=str(track.id),
            needs_discovery=False,
            extra={
                "explicit": bool(getattr(track, "explicit", False)),
                "external_urls": getattr(track, "external_urls", {}),
                "popularity": getattr(track, "popularity", 0),
            },
        )
