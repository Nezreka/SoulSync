"""YouTube Music (account) playlist source adapter.

This adapter is the signed-in account vertical: it returns the account's
own library playlists plus a virtual "Liked Music" entry. Auth reuses the
existing Settings -> YouTube cookies.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_YTMUSIC,
)
from core.youtube_music_meta import fetch_ytmusic_playlist
from core.ytmusic_library import (
    fetch_liked_music_row,
    fetch_library_playlists,
    library_playlists_to_rows,
    ytmusic_playlist_url,
)


class YTMusicPlaylistSource(PlaylistSource):
    name = SOURCE_YTMUSIC
    supports_listing = True
    supports_refresh = True
    requires_auth = True

    def __init__(self, auth_getter: Callable[[], Optional[Dict[str, str]]]):
        """``auth_getter`` matches ``web_server._ytmusic_auth_headers`` —
        zero-arg, returns a ytmusicapi browser-auth header dict or ``None``
        when Settings -> YouTube has no cookies configured. Injected (not
        called eagerly) for the same late-binding reason every other
        adapter here takes a getter."""
        self._auth_getter = auth_getter

    def _auth(self) -> Optional[Dict[str, str]]:
        try:
            return self._auth_getter()
        except Exception:
            return None

    def is_authenticated(self) -> bool:
        return bool(self._auth())

    def list_playlists(self) -> List[PlaylistMeta]:
        auth = self._auth()
        if not auth:
            return []

        raw = fetch_library_playlists(auth)
        rows = library_playlists_to_rows(raw)
        metas = [self._meta_from_row(row) for row in rows]

        # Virtual "Liked Music" playlist, pinned FIRST, count-only — matches
        # YouTube Music's own UI, where it's the prominent/first library
        # entry (deliberately unlike Spotify's "Liked Songs" / Tidal's
        # "Favorite Tracks" in this app, which are appended at the end —
        # a per-source call, not a shared convention). Omitted entirely
        # when there's nothing liked yet.
        liked_row = fetch_liked_music_row(auth)
        if liked_row:
            metas.insert(0, self._meta_from_row(liked_row))

        return metas

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        auth = self._auth()
        if not auth:
            return None
        data = fetch_ytmusic_playlist(ytmusic_playlist_url(playlist_id), auth)
        if not data:
            return None

        tracks_raw = data.get("tracks") or []
        meta = PlaylistMeta(
            source=self.name,
            source_playlist_id=playlist_id,
            name=data.get("name", "YouTube Music Playlist"),
            track_count=int(data.get("track_count", len(tracks_raw))),
            image_url=data.get("image_url") or None,
            source_url=data.get("url") or ytmusic_playlist_url(playlist_id),
        )
        tracks = [self._track_from_yt(t, idx) for idx, t in enumerate(tracks_raw) if t]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_row(self, row: Dict[str, Any]) -> PlaylistMeta:
        playlist_id = str(row["id"])
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=playlist_id,
            name=row["name"],
            owner=row.get("owner"),
            description=row.get("description"),
            image_url=row.get("image_url"),
            track_count=int(row.get("track_count") or 0),
            source_url=ytmusic_playlist_url(playlist_id),
        )

    def _track_from_yt(self, track: dict, position: int) -> NormalizedTrack:
        artists = track.get("artists") or []
        artist_name = artists[0] if artists else "Unknown Artist"
        return NormalizedTrack(
            position=position,
            track_name=track.get("name", "Unknown Track"),
            artist_name=artist_name,
            album_name=(track.get("album") or "").strip() or None,
            duration_ms=int(track.get("duration_ms", 0) or 0),
            source_track_id=str(track.get("id", "")),
            needs_discovery=False,
            extra={
                "url": track.get("url"),
                "raw_title": track.get("raw_title"),
                "raw_artist": track.get("raw_artist"),
                "video_type": track.get("video_type"),
            },
        )
