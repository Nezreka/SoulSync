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

from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_SOULSYNC_DISCOVERY,
)

logger = logging.getLogger(__name__)


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
        except Exception as exc:
            logger.warning("soulsync_discovery list_playlists failed: %s", exc)
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
        # Deliberately NOT the track_count>0 filter list_playlists() uses:
        # resolution must find empty playlists too, or a mirror of one
        # could never be refreshed back to life.
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
        # Shape first, DB second: an ID that is neither an integer row ID
        # nor an ssd_ mirror ID can't resolve — don't spend a query on it
        # (the pre-ssd_ code returned None for those without listing).
        try:
            row_id = int(sid)
        except (TypeError, ValueError):
            row_id = None
        if row_id is None and not sid.startswith('ssd_'):
            return None
        records = self._records(manager)
        if row_id is not None:
            return next((r for r in records if self._record_id(r) == row_id), None)
        return next((r for r in records if self._synthetic_id(r) == sid), None)

    @staticmethod
    def _record_id(record: Any) -> Optional[int]:
        try:
            return int(record.id)
        except (TypeError, ValueError, AttributeError):
            return None

    def _parse_synthetic(self, manager: Any, playlist_id: str) -> Optional[Tuple[str, str]]:
        """Parse an ``ssd_…`` ID into ``(kind, variant)`` via the kind registry.

        Fallback for refresh when no record exists yet for the ID (e.g. the
        personalized row was deleted after the mirror was created): the
        manager's refresh re-creates the row via ``ensure_playlist``.
        Mirrors the frontend's parsing (``autoSyncEnrichDiscoveryRows``):
        singletons match exactly, only variant kinds take a suffix, the
        longest kind wins so a shorter kind can't shadow a longer one, and
        any non-empty suffix is accepted as the variant. One deliberate
        divergence: a trailing underscore (empty variant) matches nothing
        here, where the frontend tags variant '' and keeps the row.

        The variant is NOT validated against the kind's variant resolver:
        genre_playlist's resolver enumerates only parent genres while the
        kind deliberately supports free-form variants created on demand,
        so resolver-gating would make custom-genre mirrors unrefreshable.
        A variant that produces nothing is handled by refresh_playlist's
        empty-generation guard instead (it reads as a failed refresh, so
        the mirror keeps its tracks).
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
            specs.sort(key=lambda s: len(s.kind), reverse=True)
        except Exception:
            return None
        for spec in specs:
            # A bare ssd_<kind> names a singleton only. A variant kind
            # without its variant matches nothing (the frontend drops such
            # rows as orphans) — returning (kind, '') would make the real
            # manager raise in ensure_playlist and depend on that raise to
            # stay benign.
            if rest == spec.kind and not getattr(spec, 'requires_variant', False):
                return (spec.kind, '')
            if getattr(spec, 'requires_variant', False) and rest.startswith(spec.kind + '_'):
                variant = rest[len(spec.kind) + 1:]
                if variant:
                    return (spec.kind, variant)
        return None

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """``playlist_id`` is a personalized row ID or an ``ssd_…`` mirror ID.

        Reads resolve against existing records only — there is no parse
        fallback here by design (a read must never create a row). Only
        ``refresh_playlist`` can resurrect a deleted personalized row.
        """
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
        via_fallback = record is None
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
        detail = self.get_playlist(playlist_id)
        if via_fallback and (detail is None or not detail.tracks):
            # Fallback first generation produced nothing (generator failed
            # or returned empty — e.g. a variant that names nothing real,
            # which parsing deliberately doesn't try to rule out).
            # Returning a 0-track detail would let the caller REPLACE
            # the mirror's tracks with nothing — the manager preserved no
            # snapshot here because the row was just created. Report the
            # refresh as failed instead so the mirror keeps its tracks.
            # (The record-hit path above keeps the manager's semantics: a
            # failed generation surfaces the preserved snapshot.)
            if detail is not None:
                logger.warning(
                    "soulsync_discovery refresh of %s generated 0 tracks; "
                    "reporting failure so the mirror is preserved", playlist_id)
            return None
        return detail

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
