"""Spotify public-embed playlist source adapter.

Wraps ``core.spotify_public_scraper`` — no auth, scrapes the public
embed page. ``supports_listing=False`` because there's no "user
library" to enumerate; the user pastes a URL and the adapter fetches.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_SPOTIFY_PUBLIC,
)


class SpotifyPublicPlaylistSource(PlaylistSource):
    name = SOURCE_SPOTIFY_PUBLIC
    supports_listing = False
    supports_refresh = True
    requires_auth = False

    def is_authenticated(self) -> bool:
        return True

    def list_playlists(self) -> List[PlaylistMeta]:
        return []

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is a Spotify URL or ``open.spotify.com`` URI."""
        from core.spotify_public_scraper import (
            parse_spotify_url,
            scrape_spotify_embed,
        )

        parsed = parse_spotify_url(playlist_id)
        if not parsed:
            return None

        data = scrape_spotify_embed(parsed["type"], parsed["id"])
        if not isinstance(data, dict) or data.get("error"):
            return None

        source_url = data.get("url") or playlist_id
        url_hash = data.get("url_hash") or hashlib.md5(source_url.encode()).hexdigest()[:12]
        tracks_raw = data.get("tracks") or []

        meta = PlaylistMeta(
            source=self.name,
            source_playlist_id=url_hash,
            name=data.get("name", "Spotify Playlist"),
            owner=data.get("subtitle"),
            track_count=len(tracks_raw),
            source_url=source_url,
            extra={
                "spotify_type": data.get("type"),
                "spotify_id": data.get("id"),
            },
        )

        tracks = [
            self._track_from_embed(t, idx)
            for idx, t in enumerate(tracks_raw)
            if t and t.get("id")
        ]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _track_from_embed(self, track: dict, position: int) -> NormalizedTrack:
        artists = track.get("artists") or []
        if artists and isinstance(artists[0], dict):
            artist_name = artists[0].get("name", "") or "Unknown Artist"
        elif artists:
            artist_name = str(artists[0])
        else:
            artist_name = "Unknown Artist"
        track_id = str(track.get("id", ""))
        track_name = track.get("name", "")

        # Public-embed data isn't canonical (no album art in the embed
        # response), so we DON'T set ``discovered=True``. Instead, plant
        # a ``spotify_hint`` so the downstream discovery worker can skip
        # its search step and go straight to Spotify enrichment for the
        # known track ID. Matches the pre-extraction handler behavior.
        extra: Dict[str, Any] = {
            "explicit": bool(track.get("is_explicit", False)),
            "track_number": track.get("track_number"),
        }
        if track_id:
            extra["spotify_hint"] = {
                "id": track_id,
                "name": track_name,
                "artists": artists,
            }

        return NormalizedTrack(
            position=position,
            track_name=track_name,
            artist_name=artist_name,
            album_name=None,
            duration_ms=int(track.get("duration_ms", 0) or 0),
            source_track_id=track_id,
            needs_discovery=False,
            extra=extra,
        )
