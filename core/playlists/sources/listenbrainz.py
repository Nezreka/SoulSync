"""ListenBrainz playlist source adapter.

Wraps ``core.listenbrainz_manager.ListenBrainzManager``. ListenBrainz
playlists carry only MusicBrainz recording metadata — no Spotify /
iTunes IDs — so every track returned by this adapter has
``needs_discovery=True``. Phase 1+ will route those through the
existing ``run_listenbrainz_discovery_worker`` and persist the matched
provider IDs into ``mirrored_playlist_tracks.extra_data``.

Construction takes a manager getter callable because the manager is
profile-scoped (one instance per profile, built from credentials stored
in the DB) — there is no process-wide singleton to grab at import time.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_LISTENBRAINZ,
)


class ListenBrainzPlaylistSource(PlaylistSource):
    name = SOURCE_LISTENBRAINZ
    supports_listing = True
    supports_refresh = True
    requires_auth = True

    # ListenBrainz manager caches three "playlist types" — surface all
    # three under this source. The Sync page can group / filter by
    # ``meta.extra['playlist_type']`` if it wants per-type sub-tabs.
    PLAYLIST_TYPES = ("created_for_user", "user_created", "collaborative")

    def __init__(self, manager_getter: Callable[[], Any]):
        """``manager_getter`` returns a live ``ListenBrainzManager`` for
        the current profile. ``None`` is allowed and means "no LB
        configured" — adapter degrades to empty results."""
        self._manager_getter = manager_getter

    def _manager(self):
        return self._manager_getter()

    def is_authenticated(self) -> bool:
        manager = self._manager()
        if manager is None:
            return False
        client = getattr(manager, "client", None)
        if client is None or not hasattr(client, "is_authenticated"):
            return False
        return bool(client.is_authenticated())

    def list_playlists(self) -> List[PlaylistMeta]:
        manager = self._manager()
        if manager is None:
            return []
        out: List[PlaylistMeta] = []
        for ptype in self.PLAYLIST_TYPES:
            try:
                rows = manager.get_cached_playlists(ptype) or []
            except Exception:
                rows = []
            for row in rows:
                out.append(self._meta_from_cache_row(row, ptype))
        return out

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is the ListenBrainz playlist MBID."""
        manager = self._manager()
        if manager is None:
            return None
        ptype = ""
        try:
            ptype = manager.get_playlist_type(playlist_id) or ""
        except Exception:
            ptype = ""

        cached_rows = []
        try:
            cached_rows = manager.get_cached_playlists(ptype) if ptype else []
        except Exception:
            cached_rows = []
        meta_row = next(
            (r for r in cached_rows if str(r.get("playlist_mbid")) == str(playlist_id)),
            None,
        )

        try:
            tracks_raw = manager.get_cached_tracks(playlist_id) or []
        except Exception:
            tracks_raw = []

        if meta_row is None and not tracks_raw:
            return None

        meta = self._meta_from_cache_row(
            meta_row or {"playlist_mbid": playlist_id, "track_count": len(tracks_raw)},
            ptype or "listenbrainz",
        )
        meta.track_count = len(tracks_raw)
        tracks = [self._track_from_cache_row(t, idx) for idx, t in enumerate(tracks_raw)]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """Trigger a manager-side refresh, then return the new snapshot.

        ``update_all_playlists`` is the only refresh entry-point on the
        manager — it re-fetches every cached playlist. That's wasteful
        for a single-playlist refresh; Phase 1 should add a targeted
        ``refresh_playlist(mbid)`` to the manager."""
        manager = self._manager()
        if manager is None:
            return None
        try:
            manager.update_all_playlists()
        except Exception:
            pass
        return self.get_playlist(playlist_id)

    # ---- projection helpers ------------------------------------------------

    def _meta_from_cache_row(self, row: Dict[str, Any], playlist_type: str) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(row.get("playlist_mbid", "")),
            name=row.get("title") or "ListenBrainz Playlist",
            owner=row.get("creator") or None,
            track_count=int(row.get("track_count", 0) or 0),
            extra={
                "playlist_type": playlist_type,
                "annotation": row.get("annotation") or {},
                "last_updated": row.get("last_updated"),
            },
        )

    def _track_from_cache_row(self, row: Dict[str, Any], position: int) -> NormalizedTrack:
        return NormalizedTrack(
            position=position,
            track_name=row.get("track_name", "Unknown Track"),
            artist_name=row.get("artist_name", "Unknown Artist"),
            album_name=row.get("album_name") or None,
            duration_ms=int(row.get("duration_ms", 0) or 0),
            source_track_id=row.get("recording_mbid") or None,
            image_url=row.get("album_cover_url") or None,
            needs_discovery=True,
            extra={
                "recording_mbid": row.get("recording_mbid"),
                "release_mbid": row.get("release_mbid"),
                "additional_metadata": row.get("additional_metadata"),
            },
        )
