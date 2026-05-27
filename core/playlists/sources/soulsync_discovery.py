"""SoulSync Discovery (personalized playlists) source adapter.

Wraps ``core.personalized.manager.PersonalizedPlaylistManager``. Unlike
ListenBrainz / Last.fm, personalized playlists already carry source
IDs (Spotify / iTunes / Deezer track IDs) — they were built from
``discovery_pool`` rows. ``needs_discovery=False`` on every track.

Playlist IDs here are the integer DB row IDs (``personalized_playlists.id``)
converted to strings so the unified interface stays string-keyed. The
adapter parses them back to ints when calling the manager.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_SOULSYNC_DISCOVERY,
)


class SoulSyncDiscoveryPlaylistSource(PlaylistSource):
    name = SOURCE_SOULSYNC_DISCOVERY
    supports_listing = True
    supports_refresh = True
    requires_auth = False

    def __init__(
        self,
        manager_getter: Callable[[], Any],
        profile_id_getter: Optional[Callable[[], int]] = None,
    ):
        self._manager_getter = manager_getter
        self._profile_id_getter = profile_id_getter or (lambda: 1)

    def _manager(self):
        return self._manager_getter()

    def is_authenticated(self) -> bool:
        return self._manager() is not None

    def list_playlists(self) -> List[PlaylistMeta]:
        manager = self._manager()
        if manager is None:
            return []
        try:
            records = manager.list_playlists(profile_id=self._profile_id_getter()) or []
        except Exception:
            return []
        return [self._meta_from_record(r) for r in records]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is the stringified ``personalized_playlists.id``."""
        manager = self._manager()
        if manager is None:
            return None
        try:
            row_id = int(playlist_id)
        except (TypeError, ValueError):
            return None

        records = []
        try:
            records = manager.list_playlists(profile_id=self._profile_id_getter()) or []
        except Exception:
            records = []
        record = next((r for r in records if int(r.id) == row_id), None)
        if record is None:
            return None

        try:
            tracks_raw = manager.get_playlist_tracks(row_id) or []
        except Exception:
            tracks_raw = []

        meta = self._meta_from_record(record)
        meta.track_count = len(tracks_raw)
        tracks = [self._track_from_record(t, idx) for idx, t in enumerate(tracks_raw)]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        manager = self._manager()
        if manager is None:
            return None
        try:
            row_id = int(playlist_id)
        except (TypeError, ValueError):
            return None

        records = manager.list_playlists(profile_id=self._profile_id_getter()) or []
        record = next((r for r in records if int(r.id) == row_id), None)
        if record is None:
            return None

        try:
            manager.refresh_playlist(
                kind=record.kind,
                variant=record.variant,
                profile_id=record.profile_id,
            )
        except Exception:  # noqa: S110 — manager persists last_generation_error on failure; surface existing snapshot
            pass
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_record(self, record: Any) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(record.id),
            name=record.name,
            track_count=int(record.track_count or 0),
            description=record.kind,
            extra={
                "kind": record.kind,
                "variant": record.variant,
                "profile_id": record.profile_id,
                "is_stale": bool(record.is_stale),
                "last_generated_at": record.last_generated_at,
                "last_synced_at": record.last_synced_at,
                "last_generation_source": record.last_generation_source,
                "last_generation_error": record.last_generation_error,
            },
        )

    def _track_from_record(self, track: Any, position: int) -> NormalizedTrack:
        primary_id = track.primary_id()
        return NormalizedTrack(
            position=position,
            track_name=track.track_name,
            artist_name=track.artist_name,
            album_name=track.album_name or None,
            duration_ms=int(track.duration_ms or 0),
            source_track_id=primary_id,
            image_url=track.album_cover_url or None,
            needs_discovery=False,
            extra={
                "spotify_track_id": track.spotify_track_id,
                "itunes_track_id": track.itunes_track_id,
                "deezer_track_id": track.deezer_track_id,
                "popularity": track.popularity,
                "track_data_json": track.track_data_json,
                "source_hint": track.source,
            },
        )
