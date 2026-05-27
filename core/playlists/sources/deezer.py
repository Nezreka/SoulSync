"""Deezer playlist source adapter.

Wraps ``core.deezer_client.DeezerClient.get_playlist``. Deezer's public
API needs no auth, so ``is_authenticated`` always returns True. Listing
the *user's* playlists requires OAuth — surfaced via the underlying
``is_user_authenticated`` flag — but the get-by-id flow works on any
public playlist regardless.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_DEEZER,
)


class DeezerPlaylistSource(PlaylistSource):
    name = SOURCE_DEEZER
    supports_listing = True   # user playlists need OAuth; falls back to []
    supports_refresh = True
    requires_auth = False

    def __init__(self, client_getter: Callable[[], Any]):
        self._client_getter = client_getter

    def _client(self):
        return self._client_getter()

    def is_authenticated(self) -> bool:
        client = self._client()
        if client is None:
            return False
        # Deezer's `is_authenticated` is True even with no OAuth token —
        # the public API works without one. Use that as our liveness signal.
        return bool(client.is_authenticated())

    def list_playlists(self) -> List[PlaylistMeta]:
        client = self._client()
        if client is None:
            return []
        # User playlists need OAuth; `get_user_playlists` returns [] when
        # the stub-interface variant is in use. Honor whatever the client
        # actually returns.
        try:
            playlists = client.get_user_playlists() or []
        except Exception:
            return []
        return [self._meta_from_playlist(p) for p in playlists]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        client = self._client()
        if client is None:
            return None
        data = client.get_playlist(playlist_id)
        if not data:
            return None
        meta = self._meta_from_dict(data)
        tracks_raw = data.get("tracks") or []
        tracks = [self._track_from_dict(t, idx) for idx, t in enumerate(tracks_raw)]
        meta.track_count = len(tracks)
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_playlist(self, playlist: Any) -> PlaylistMeta:
        """Project a ``DeezerClient.Playlist`` dataclass into PlaylistMeta."""
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(getattr(playlist, "id", "")),
            name=getattr(playlist, "name", "Deezer Playlist"),
            description=getattr(playlist, "description", None),
            track_count=int(getattr(playlist, "total_tracks", 0) or 0),
            owner=getattr(playlist, "owner", None),
        )

    def _meta_from_dict(self, p: Dict[str, Any]) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(p.get("id", "")),
            name=p.get("name", "Deezer Playlist"),
            description=p.get("description") or None,
            owner=p.get("owner") or None,
            image_url=p.get("image_url") or None,
            track_count=int(p.get("track_count", 0) or 0),
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
            needs_discovery=False,
            extra={"track_number": t.get("track_number")},
        )
