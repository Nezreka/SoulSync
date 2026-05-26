"""Spotify playlist source adapter.

Wraps ``core.spotify_client.SpotifyClient`` — the authenticated Spotify
client used everywhere else. Adapter projects Spotify's ``Playlist`` /
``Track`` dataclasses into ``PlaylistMeta`` / ``NormalizedTrack``.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_SPOTIFY,
)


class SpotifyPlaylistSource(PlaylistSource):
    name = SOURCE_SPOTIFY
    supports_listing = True
    supports_refresh = True
    requires_auth = True

    def __init__(self, client_getter: Callable[[], Any]):
        """``client_getter`` returns the live ``SpotifyClient`` singleton.

        We accept a getter (not the client itself) so the adapter can be
        constructed at import time, before ``web_server.py`` has wired
        up the singleton."""
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
        playlists = client.get_user_playlists_metadata_only()
        return [self._meta_from_playlist(p) for p in playlists]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        client = self._client()
        if client is None or not client.is_authenticated():
            return None
        playlist = client.get_playlist_by_id(playlist_id)
        if playlist is None:
            return None
        meta = self._meta_from_playlist(playlist)
        tracks = [self._track_from_spotify(t, idx) for idx, t in enumerate(playlist.tracks)]
        meta.track_count = len(tracks)
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_playlist(self, playlist: Any) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(playlist.id),
            name=playlist.name,
            owner=playlist.owner,
            description=playlist.description,
            track_count=int(getattr(playlist, "total_tracks", 0) or 0),
            extra={
                "public": bool(getattr(playlist, "public", False)),
                "collaborative": bool(getattr(playlist, "collaborative", False)),
            },
        )

    def _track_from_spotify(self, track: Any, position: int) -> NormalizedTrack:
        artists = getattr(track, "artists", None) or []
        artist_name = ", ".join(artists) if artists else "Unknown Artist"
        return NormalizedTrack(
            position=position,
            track_name=track.name,
            artist_name=artist_name,
            album_name=getattr(track, "album", None),
            duration_ms=int(getattr(track, "duration_ms", 0) or 0),
            source_track_id=str(track.id),
            image_url=getattr(track, "image_url", None),
            needs_discovery=False,
            extra={
                "popularity": getattr(track, "popularity", 0),
                "external_urls": getattr(track, "external_urls", None),
                "preview_url": getattr(track, "preview_url", None),
            },
        )
