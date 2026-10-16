"""SoulSync Discovery (personalized playlists) source adapter.

Wraps ``core.personalized.manager.PersonalizedPlaylistManager``. Unlike
ListenBrainz / Last.fm, personalized playlists already carry source
IDs (Spotify / iTunes / Deezer track IDs) — they were built from
``discovery_pool`` rows. ``needs_discovery=False`` on every track.

Playlist IDs here are the integer DB row IDs (``personalized_playlists.id``)
converted to strings so the unified interface stays string-keyed. The
adapter parses them back to ints when calling the manager.

Mirrors created by the Sync page's "Refresh & Mirror" action carry a
different ID shape, though: a synthetic ``ssd_<kind>[_<variant>]`` string
(the frontend's UPSERT-stable mirror key — see
``webui/static/sync-soulsync-discovery.js``). Those mirrors are exactly
the ones users put on an Auto-Sync schedule, so the adapter MUST resolve
the synthetic shape too: refresh used to ``int()``-parse the ID, fail on
``ssd_…``, and return None — the pipeline then synced the mirror's stale
stored tracks forever and a scheduled run could never generate a new
shuffle (#1724).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
        # Only surface syncable playlists: skip ones that generated empty
        # (e.g. a no-op generator) — a 0-track playlist can't be mirrored and
        # is just clutter on the sync board. (Unregistered kinds are already
        # dropped by the manager.)
        records = [r for r in records if (getattr(r, 'track_count', 0) or 0) > 0]
        return [self._meta_from_record(r) for r in records]

    # ---- ID resolution -----------------------------------------------------

    @staticmethod
    def _synthetic_id(record: Any) -> str:
        """The frontend's mirror key for a record: ``ssd_<kind>[_<variant>]``."""
        kind = getattr(record, 'kind', '') or ''
        variant = getattr(record, 'variant', '') or ''
        return f"ssd_{kind}_{variant}" if variant else f"ssd_{kind}"

    def _records(self, manager: Any) -> List[Any]:
        try:
            return manager.list_playlists(profile_id=self._profile_id_getter()) or []
        except Exception as exc:
            # Callers treat "no records" as "no such playlist" (the refresh
            # handler then logs its own 'no tracks returned' error), but a
            # swallowed DB failure must not be indistinguishable in the logs.
            logger.warning("soulsync_discovery list_playlists failed: %s", exc)
            return []

    def _find_record(self, manager: Any, playlist_id: str) -> Optional[Any]:
        """Resolve an integer row ID *or* an ``ssd_…`` synthetic ID to a record.

        Synthetic resolution is an exact match against each record's own
        synthetic ID — never a string split — so kinds containing
        underscores (``discovery_shuffle``, ``time_machine``) and variants
        containing underscores can't be misparsed.
        """
        sid = str(playlist_id or '').strip()
        if not sid:
            return None
        records = self._records(manager)
        try:
            row_id = int(sid)
        except (TypeError, ValueError):
            row_id = None
        if row_id is not None:
            return next((r for r in records if int(r.id) == row_id), None)
        if sid.startswith('ssd_'):
            return next((r for r in records if self._synthetic_id(r) == sid), None)
        return None

    def _parse_synthetic(self, manager: Any, playlist_id: str) -> Optional[Tuple[str, str]]:
        """Parse an ``ssd_…`` ID into ``(kind, variant)`` via the kind registry.

        Fallback for refresh when no record exists yet for the ID (e.g. the
        personalized row was deleted after the mirror was created): the
        manager's refresh re-creates the row via ``ensure_playlist``.
        Mirrors the frontend's parsing (``autoSyncEnrichDiscoveryRows``):
        singletons match exactly, only variant kinds take a suffix, and the
        longest kind wins so a shorter kind can't shadow a longer one.
        Returns None when the ID names no registered kind.
        """
        sid = str(playlist_id or '').strip()
        if not sid.startswith('ssd_'):
            return None
        rest = sid[len('ssd_'):]
        registry = getattr(manager, 'registry', None)
        all_specs = getattr(registry, 'all', None)
        if not callable(all_specs):
            return None
        try:
            specs = [s for s in (all_specs() or []) if getattr(s, 'kind', None)]
        except Exception:
            return None
        for spec in sorted(specs, key=lambda s: len(s.kind), reverse=True):
            if rest == spec.kind:
                return (spec.kind, '')
            if getattr(spec, 'requires_variant', False) and rest.startswith(spec.kind + '_'):
                variant = rest[len(spec.kind) + 1:]
                if variant and self._variant_is_valid(manager, spec, variant):
                    return (spec.kind, variant)
        return None

    @staticmethod
    def _variant_is_valid(manager: Any, spec: Any, variant: str) -> bool:
        """Check a parsed variant against the kind's variant resolver.

        Without this, a stale/garbled ID like ``ssd_daily_mix_99`` would
        make refresh create a personalized row for a variant that doesn't
        exist. Kinds without a resolver (or whose resolver fails or
        returns nothing) can't be validated — accept, since the exact
        record-match path above remains the primary resolution and this
        fallback only runs when no row exists yet.
        """
        resolver = getattr(spec, 'variant_resolver', None)
        if not callable(resolver):
            return True
        try:
            valid = resolver(getattr(manager, 'deps', None))
        except Exception:
            return True
        if not valid:
            return True
        return variant in [str(v) for v in valid]

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is a personalized row ID or an ``ssd_…`` mirror ID."""
        manager = self._manager()
        if manager is None:
            return None
        record = self._find_record(manager, playlist_id)
        if record is None:
            return None

        try:
            tracks_raw = manager.get_playlist_tracks(int(record.id)) or []
        except Exception:
            tracks_raw = []

        meta = self._meta_from_record(record)
        meta.track_count = len(tracks_raw)
        tracks = [self._track_from_record(t, idx) for idx, t in enumerate(tracks_raw)]
        return PlaylistDetail(meta=meta, tracks=tracks)

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """Regenerate the playlist, then return its fresh snapshot.

        ``playlist_id`` is a personalized row ID or an ``ssd_…`` mirror ID.
        On generator failure the existing snapshot is surfaced instead
        (the manager records the error on the row); None means the ID
        names no known playlist or registered kind.
        """
        manager = self._manager()
        if manager is None:
            return None
        record = self._find_record(manager, playlist_id)
        if record is not None:
            kind, variant = record.kind, record.variant
            # PlaylistRecord always carries profile_id; the getattr guard
            # only protects duck-typed manager doubles.
            profile_id = getattr(record, 'profile_id', None) or self._profile_id_getter()
        else:
            # No record in the current profile: regenerate from the parsed
            # ID under the current profile (the adapter interface carries no
            # mirror-owner profile; background/automation runs resolve the
            # getter to the automation owner's profile, so this is the
            # owner's profile in the scheduled case this fallback serves).
            parsed = self._parse_synthetic(manager, playlist_id)
            if parsed is None:
                return None
            kind, variant = parsed
            profile_id = self._profile_id_getter()

        try:
            manager.refresh_playlist(
                kind=kind,
                variant=variant,
                profile_id=profile_id,
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
