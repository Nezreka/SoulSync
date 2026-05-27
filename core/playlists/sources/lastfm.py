"""Last.fm radio playlist source adapter.

Last.fm radio playlists are persisted by
``ListenBrainzManager.save_lastfm_radio_playlist`` under
``playlist_type='lastfm_radio'`` in the ``listenbrainz_playlists``
table — they share the same storage as ListenBrainz playlists but
originate from Last.fm's similar-tracks API.

Like ListenBrainz, tracks are MB metadata only, so ``needs_discovery``
is True on every track.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_LASTFM,
)
from core.playlists.sources.listenbrainz import (
    DiscoverCallable,
    ListenBrainzPlaylistSource,
)


LASTFM_PLAYLIST_TYPE = "lastfm_radio"


class LastFMPlaylistSource(PlaylistSource):
    name = SOURCE_LASTFM
    supports_listing = True
    supports_refresh = False  # Refresh requires re-running the radio generator
    requires_auth = True

    def __init__(
        self,
        manager_getter: Callable[[], Any],
        discover_callable: Optional[DiscoverCallable] = None,
    ):
        """``manager_getter`` returns the profile's ``ListenBrainzManager``
        (Last.fm radio playlists share that storage layer).

        ``discover_callable`` runs matching-engine + provider search;
        Last.fm radio tracks are MB-metadata only, so this is needed
        for ``discover_tracks`` to do real work."""
        self._manager_getter = manager_getter
        self._discover_callable = discover_callable

    def _manager(self):
        return self._manager_getter()

    def is_authenticated(self) -> bool:
        # Last.fm radio rows exist independently of any auth state —
        # they're persisted snapshots. Treat "manager exists" as enough.
        return self._manager() is not None

    def list_playlists(self) -> List[PlaylistMeta]:
        manager = self._manager()
        if manager is None:
            return []
        try:
            rows = manager.get_cached_playlists(LASTFM_PLAYLIST_TYPE) or []
        except Exception:
            rows = []
        return [self._meta_from_cache_row(r) for r in rows]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        manager = self._manager()
        if manager is None:
            return None
        try:
            rows = manager.get_cached_playlists(LASTFM_PLAYLIST_TYPE) or []
        except Exception:
            rows = []
        meta_row = next(
            (r for r in rows if str(r.get("playlist_mbid")) == str(playlist_id)),
            None,
        )
        if meta_row is None:
            return None
        try:
            tracks_raw = manager.get_cached_tracks(playlist_id) or []
        except Exception:
            tracks_raw = []
        meta = self._meta_from_cache_row(meta_row)
        meta.track_count = len(tracks_raw)
        tracks = [self._track_from_cache_row(t, idx) for idx, t in enumerate(tracks_raw)]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        # Regenerating a Last.fm radio playlist needs the seed track +
        # the Last.fm client — that lives outside this adapter. Phase 1
        # wires up the regeneration; Phase 0 just returns the current
        # snapshot.
        return self.get_playlist(playlist_id)

    # Discovery shares the LB adapter's implementation — same track
    # shape (MB metadata), same matching needs.
    discover_tracks = ListenBrainzPlaylistSource.discover_tracks

    # ---- projection helpers ------------------------------------------------

    def _meta_from_cache_row(self, row: Dict[str, Any]) -> PlaylistMeta:
        return PlaylistMeta(
            source=self.name,
            source_playlist_id=str(row.get("playlist_mbid", "")),
            name=row.get("title") or "Last.fm Radio",
            owner=row.get("creator") or "Last.fm",
            track_count=int(row.get("track_count", 0) or 0),
            extra={
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
            },
        )
