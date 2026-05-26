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


# Type alias for the discovery callable: takes a list of MB-shaped
# track dicts, returns a parallel list of matched_data dicts (or None
# when no match). Kept narrow so test stubs are easy.
DiscoverCallable = Callable[[List[Dict[str, Any]]], List[Optional[Dict[str, Any]]]]


class ListenBrainzPlaylistSource(PlaylistSource):
    name = SOURCE_LISTENBRAINZ
    supports_listing = True
    supports_refresh = True
    requires_auth = True

    # ListenBrainz manager caches three "playlist types" — surface all
    # three under this source. The Sync page can group / filter by
    # ``meta.extra['playlist_type']`` if it wants per-type sub-tabs.
    PLAYLIST_TYPES = ("created_for_user", "user_created", "collaborative")

    def __init__(
        self,
        manager_getter: Callable[[], Any],
        discover_callable: Optional[DiscoverCallable] = None,
    ):
        """``manager_getter`` returns a live ``ListenBrainzManager`` for
        the current profile. ``None`` is allowed and means "no LB
        configured" — adapter degrades to empty results.

        ``discover_callable`` runs the actual matching-engine + provider
        search. ``None`` means no discovery is wired (Phase 0 default):
        ``discover_tracks`` returns the input list unchanged."""
        self._manager_getter = manager_getter
        self._discover_callable = discover_callable

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
        """``playlist_id`` is the ListenBrainz playlist MBID, OR a
        synthetic series id (e.g. ``lb_weekly_jams_<user>``) that
        resolves to the newest member of a rotating series."""
        manager = self._manager()
        if manager is None:
            return None

        # Rolling-series resolution: synthetic ids look up the
        # latest matching cache row and continue with that MBID.
        from core.playlists.lb_series import is_series_synthetic_id
        if is_series_synthetic_id(playlist_id):
            resolved_mbid = self._resolve_series_to_latest_mbid(manager, playlist_id)
            if not resolved_mbid:
                return None
            return self._fetch_playlist_by_mbid(manager, resolved_mbid, override_meta_id=playlist_id)

        return self._fetch_playlist_by_mbid(manager, playlist_id)

    def _fetch_playlist_by_mbid(
        self,
        manager: Any,
        playlist_mbid: str,
        override_meta_id: Optional[str] = None,
    ) -> Optional[PlaylistDetail]:
        """Resolve a real LB playlist MBID into a PlaylistDetail.

        ``override_meta_id`` lets the rolling-series path keep the
        synthetic id on the meta object so the caller can write the
        mirror row back under that id."""
        ptype = ""
        try:
            ptype = manager.get_playlist_type(playlist_mbid) or ""
        except Exception:
            ptype = ""

        cached_rows = []
        try:
            cached_rows = manager.get_cached_playlists(ptype) if ptype else []
        except Exception:
            cached_rows = []
        meta_row = next(
            (r for r in cached_rows if str(r.get("playlist_mbid")) == str(playlist_mbid)),
            None,
        )

        try:
            tracks_raw = manager.get_cached_tracks(playlist_mbid) or []
        except Exception:
            tracks_raw = []

        if meta_row is None and not tracks_raw:
            return None

        meta = self._meta_from_cache_row(
            meta_row or {"playlist_mbid": playlist_mbid, "track_count": len(tracks_raw)},
            ptype or "listenbrainz",
        )
        if override_meta_id:
            meta.source_playlist_id = override_meta_id
        meta.track_count = len(tracks_raw)
        tracks = [self._track_from_cache_row(t, idx) for idx, t in enumerate(tracks_raw)]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def _resolve_series_to_latest_mbid(self, manager: Any, series_id: str) -> Optional[str]:
        """Find the newest LB cache row matching a series synthetic id.

        Series synthetic ids encode both the series type and the
        ListenBrainz username. We query the LB cache (via the
        manager's DB connection) for the row whose title matches the
        series' LIKE pattern and has the most recent ``last_updated``,
        then return that row's MBID for normal fetching downstream."""
        try:
            # The synthetic id alone doesn't carry the title pattern,
            # so we re-derive it from any per-period sibling that's
            # already in the cache. Iterate the known series specs and
            # ask which one this synthetic id belongs to.
            from core.playlists.lb_series import _SERIES_PATTERNS
            spec = None
            user_token = ""
            for entry in _SERIES_PATTERNS:
                series_prefix = entry["series_format"].format(user="").rstrip("_") + "_"
                if series_id.startswith(series_prefix):
                    spec = entry
                    user_token = series_id[len(series_prefix):]
                    break
            if spec is None or not user_token:
                return None
            like_pattern = spec["like_format"].format(user=user_token)

            # Query the LB cache for the newest matching row. The
            # manager's connection helper returns a plain sqlite3
            # connection — explicit try/finally for close parity with
            # the manager's own usage pattern.
            conn = manager._get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT playlist_mbid FROM listenbrainz_playlists
                    WHERE profile_id = ? AND title LIKE ?
                    ORDER BY last_updated DESC
                    LIMIT 1
                    """,
                    (manager.profile_id, like_pattern),
                )
                row = cur.fetchone()
            finally:
                conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def discover_tracks(self, tracks: List[NormalizedTrack]) -> List[NormalizedTrack]:
        """Run each MB-metadata track through the matching engine.

        Tracks with ``needs_discovery=False`` (e.g. already-matched
        survivors of a previous refresh) pass through unchanged.
        Matched tracks get ``extra['discovered']=True`` + a
        ``matched_data`` block so the projection helper can produce
        the canonical ``extra_data`` JSON; ``needs_discovery`` flips
        to False on them.

        Unmatched tracks stay ``needs_discovery=True`` so the caller
        can decide how to handle them (wing-it stub, skip, retry)."""
        if not tracks or self._discover_callable is None:
            return tracks

        to_match: List[Dict[str, Any]] = []
        match_indices: List[int] = []
        for idx, t in enumerate(tracks):
            if not t.needs_discovery:
                continue
            to_match.append({
                "track_name": t.track_name,
                "artist_name": t.artist_name,
                "album_name": t.album_name or "",
                "duration_ms": t.duration_ms or 0,
            })
            match_indices.append(idx)

        if not to_match:
            return tracks

        try:
            matched = self._discover_callable(to_match) or []
        except Exception:
            return tracks

        out = list(tracks)
        for slot_idx, result in zip(match_indices, matched):
            if not result:
                continue
            track = out[slot_idx]
            provider = result.pop("_provider", None) or "unknown"
            confidence = result.pop("_confidence", None)
            new_extra = dict(track.extra or {})
            new_extra["discovered"] = True
            new_extra["provider"] = provider
            if confidence is not None:
                new_extra["confidence"] = confidence
            new_extra["matched_data"] = result
            out[slot_idx] = NormalizedTrack(
                position=track.position,
                track_name=track.track_name,
                artist_name=track.artist_name,
                album_name=track.album_name,
                duration_ms=track.duration_ms,
                source_track_id=result.get("id") or track.source_track_id,
                image_url=result.get("image_url") or track.image_url,
                needs_discovery=False,
                extra=new_extra,
            )
        return out

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
