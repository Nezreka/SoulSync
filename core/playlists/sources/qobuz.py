"""Qobuz playlist source adapter.

Wraps ``core.qobuz_client.QobuzClient``. The client already returns
playlist/track dicts in a normalized Sync-page shape (see
``_normalize_qobuz_playlist`` / ``_normalize_qobuz_track``), so the
adapter is mostly a key remap.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_QOBUZ,
)


class QobuzPlaylistSource(PlaylistSource):
    name = SOURCE_QOBUZ
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
        playlists = client.get_user_playlists() or []
        return [self._meta_from_dict(p) for p in playlists]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        client = self._client()
        if client is None or not client.is_authenticated():
            return None
        playlist = client.get_playlist(playlist_id)
        if not playlist:
            return None
        meta = self._meta_from_dict(playlist)
        tracks_raw = playlist.get("tracks") or []
        tracks = [self._track_from_dict(t, idx) for idx, t in enumerate(tracks_raw)]
        meta.track_count = len(tracks)
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_dict(self, p: Dict[str, Any]) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(p.get("id", "")),
            name=p.get("name", "Qobuz Playlist"),
            description=p.get("description") or None,
            image_url=p.get("image_url") or None,
            track_count=int(p.get("track_count", 0) or 0),
            extra={
                "public": bool(p.get("public", False)),
                "external_urls": p.get("external_urls", {}),
            },
        )

    def _track_from_dict(self, t: Dict[str, Any], position: int) -> NormalizedTrack:
        artists = t.get("artists") or []
        artist_name = artists[0] if artists else "Unknown Artist"
        return NormalizedTrack(
            position=position,
            track_name=t.get("name", "Unknown Track"),
            artist_name=artist_name,
            album_name=t.get("album") or None,
            duration_ms=int(t.get("duration_ms", 0) or 0),
            source_track_id=str(t.get("id", "")),
            image_url=t.get("image_url") or None,
            needs_discovery=False,
            extra={k: v for k, v in t.items() if k not in {
                "id", "name", "artists", "album", "duration_ms", "image_url",
            }},
        )
