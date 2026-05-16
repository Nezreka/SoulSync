"""Storage layer + lifecycle for personalized playlists.

The manager is the ONLY place that touches the
``personalized_playlists`` / ``personalized_playlist_tracks`` /
``personalized_track_history`` tables. Generators (in
``core/personalized/generators/``) produce track lists; the manager
persists, refreshes, and serves them.

Key invariants:

- ``(profile_id, kind, variant)`` uniquely identifies a playlist.
  Variant '' (empty string) means singleton — used for kinds like
  ``hidden_gems`` that don't have multiple instances per profile.
- A playlist row is auto-created on first access (``ensure_playlist``)
  using the kind's default config.
- Track lists are atomically replaced on refresh — never partial-
  mutated. Either the new snapshot lands fully or the old one
  remains.
- Refresh failures get logged on the row
  (``last_generation_error``) so the UI can surface them without
  losing the previous good snapshot.
- Staleness history is append-only and queried by the
  ``exclude_recent_days`` config option (handled by individual
  generators when they want to honor it).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from core.personalized.specs import PlaylistKindRegistry, get_registry
from core.personalized.types import PlaylistConfig, PlaylistRecord, Track

logger = get_logger("personalized.manager")


class PersonalizedPlaylistManager:
    """Owns persistence + refresh lifecycle for personalized playlists."""

    def __init__(self, database, deps: Any, registry: Optional[PlaylistKindRegistry] = None):
        """
        Args:
            database: MusicDatabase singleton (exposes ``_get_connection``).
            deps: Opaque object passed through to each generator
                callable. Whatever a generator needs (the legacy
                ``PersonalizedPlaylistsService`` instance, the
                ``config_manager``, a metadata client) goes in here.
                Manager doesn't inspect it.
            registry: optional PlaylistKindRegistry override (for tests).
        """
        self.database = database
        self.deps = deps
        self.registry = registry or get_registry()

    # ─── playlist row lifecycle ──────────────────────────────────────

    def ensure_playlist(self, kind: str, variant: str = '', profile_id: int = 1) -> PlaylistRecord:
        """Return the playlist row for ``(profile, kind, variant)``,
        creating it from the kind's default config if it doesn't exist."""
        spec = self.registry.get(kind)
        if spec is None:
            raise ValueError(f"Unknown playlist kind: {kind!r}")
        if spec.requires_variant and not variant:
            raise ValueError(f"Kind {kind!r} requires a variant")

        existing = self._fetch_playlist_row(kind, variant, profile_id)
        if existing is not None:
            return existing

        # Insert new row using the kind's defaults.
        config = spec.default_config
        name = spec.display_name(variant)
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO personalized_playlists
                    (profile_id, kind, variant, name, config_json,
                     track_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (profile_id, kind, variant, name, json.dumps(config.to_json_dict())),
            )
            conn.commit()
        return self._fetch_playlist_row(kind, variant, profile_id)  # type: ignore[return-value]

    def get_playlist(self, kind: str, variant: str = '', profile_id: int = 1) -> Optional[PlaylistRecord]:
        """Return the playlist row if it exists. Does NOT auto-create."""
        return self._fetch_playlist_row(kind, variant, profile_id)

    def list_playlists(self, profile_id: int = 1) -> List[PlaylistRecord]:
        """List every persisted playlist for a profile, newest-first."""
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, profile_id, kind, variant, name, config_json,
                       track_count, last_generated_at, last_synced_at,
                       last_generation_source, last_generation_error
                FROM personalized_playlists
                WHERE profile_id = ?
                ORDER BY COALESCE(last_generated_at, created_at) DESC
                """,
                (profile_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_record(r) for r in rows]

    def update_config(self, kind: str, variant: str, profile_id: int, overrides: Dict[str, Any]) -> PlaylistRecord:
        """Patch the per-playlist config with the provided overrides."""
        record = self.ensure_playlist(kind, variant, profile_id)
        merged = record.config.merged(overrides)
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE personalized_playlists
                SET config_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(merged.to_json_dict()), record.id),
            )
            conn.commit()
        return self._fetch_playlist_row(kind, variant, profile_id)  # type: ignore[return-value]

    # ─── refresh / generation ─────────────────────────────────────────

    def refresh_playlist(
        self,
        kind: str,
        variant: str = '',
        profile_id: int = 1,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> PlaylistRecord:
        """Run the kind's generator and persist the result as the
        playlist's current snapshot.

        Atomic: track list is replaced in a single transaction. On
        generator exception, the previous snapshot is preserved and
        the error is recorded on the row.

        Args:
            kind: registered kind identifier.
            variant: e.g. '1980s' for time machine, '' for singletons.
            profile_id: profile to refresh under.
            config_overrides: optional per-call config tweaks merged on
                top of the stored config (e.g. UI lets the user "preview
                with limit=100" without persisting that change).

        Returns:
            Updated PlaylistRecord with fresh ``track_count`` /
            ``last_generated_at`` (or ``last_generation_error`` on
            failure).
        """
        spec = self.registry.get(kind)
        if spec is None:
            raise ValueError(f"Unknown playlist kind: {kind!r}")
        record = self.ensure_playlist(kind, variant, profile_id)

        config = record.config
        if config_overrides:
            config = config.merged(config_overrides)

        try:
            tracks = spec.generator(self.deps, variant, config)
        except Exception as exc:  # noqa: BLE001 — record + re-raise after persisting
            logger.exception("Generator failed for kind=%s variant=%s: %s", kind, variant, exc)
            self._record_generation_failure(record.id, str(exc))
            return self._fetch_playlist_row(kind, variant, profile_id)  # type: ignore[return-value]

        # Quality post-filters — applied uniformly to every kind so
        # generators stay focused on selection logic, not staleness
        # bookkeeping. Filters are config-driven; defaults preserve
        # the pre-overhaul behavior (no filtering).
        tracks = self._apply_quality_filters(tracks, kind, profile_id, config)

        return self._persist_snapshot(record.id, kind, profile_id, tracks)

    def _apply_quality_filters(
        self,
        tracks: List[Track],
        kind: str,
        profile_id: int,
        config: PlaylistConfig,
    ) -> List[Track]:
        """Apply manager-level quality filters to a generator's output.

        Currently:
        - **Staleness window** (`config.exclude_recent_days > 0`): drops
          any track whose primary id was served by this `kind` for this
          `profile_id` in the last N days. Prevents the same track
          from showing up across consecutive refreshes — e.g. a daily
          Discovery Shuffle that shouldn't replay yesterday's picks.
          Tracks without a primary id pass through unchanged (nothing
          to dedupe on).

        Returns a new list (never mutates input). When no filter
        applies, returns ``tracks`` unchanged."""
        if config.exclude_recent_days <= 0 or not tracks:
            return tracks

        recent_set = set(self.recent_track_ids(profile_id, kind, config.exclude_recent_days))
        if not recent_set:
            return tracks

        return [t for t in tracks if not t.primary_id() or t.primary_id() not in recent_set]

    # ─── track read ──────────────────────────────────────────────────

    def get_playlist_tracks(self, playlist_id: int) -> List[Track]:
        """Return the persisted track list for a playlist row."""
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT spotify_track_id, itunes_track_id, deezer_track_id,
                       track_name, artist_name, album_name, album_cover_url,
                       duration_ms, popularity, track_data_json
                FROM personalized_playlist_tracks
                WHERE playlist_id = ?
                ORDER BY position ASC
                """,
                (playlist_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_track(r) for r in rows]

    # ─── staleness history ───────────────────────────────────────────

    def recent_track_ids(self, profile_id: int, kind: str, days: int) -> List[str]:
        """Return track IDs served by ``kind`` for ``profile_id`` in
        the last ``days`` days. Generators query this when honoring
        the ``exclude_recent_days`` config knob."""
        if days <= 0:
            return []
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT track_id
                FROM personalized_track_history
                WHERE profile_id = ?
                  AND kind = ?
                  AND served_at >= datetime('now', ?)
                """,
                (profile_id, kind, f'-{int(days)} days'),
            )
            return [r[0] for r in cursor.fetchall() if r[0]]

    # ─── internal helpers ────────────────────────────────────────────

    def _persist_snapshot(self, playlist_id: int, kind: str, profile_id: int, tracks: List[Track]) -> PlaylistRecord:
        """Atomic replace of a playlist's track list + history append."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        primary_source = next(
            (t.source for t in tracks if t.source), None,
        )
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN")
                cursor.execute(
                    "DELETE FROM personalized_playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                )
                for position, track in enumerate(tracks):
                    td = track.track_data_json
                    if td is not None and not isinstance(td, str):
                        td = json.dumps(td)
                    cursor.execute(
                        """
                        INSERT INTO personalized_playlist_tracks
                            (playlist_id, position,
                             spotify_track_id, itunes_track_id, deezer_track_id,
                             track_name, artist_name, album_name, album_cover_url,
                             duration_ms, popularity, track_data_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            playlist_id, position,
                            track.spotify_track_id, track.itunes_track_id, track.deezer_track_id,
                            track.track_name, track.artist_name, track.album_name, track.album_cover_url,
                            int(track.duration_ms or 0), int(track.popularity or 0), td,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE personalized_playlists
                    SET track_count = ?, last_generated_at = CURRENT_TIMESTAMP,
                        last_generation_source = ?, last_generation_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (len(tracks), primary_source, playlist_id),
                )
                # History append — only for tracks with a primary ID,
                # used by exclude_recent_days filter on next refresh.
                for track in tracks:
                    tid = track.primary_id()
                    if not tid:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO personalized_track_history
                            (profile_id, kind, track_id, served_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (profile_id, kind, tid),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # Re-read the row so the returned record carries the fresh
        # last_generated_at timestamp.
        record = self._fetch_playlist_row_by_id(playlist_id)
        if record is None:
            raise RuntimeError(f"playlist row {playlist_id} disappeared mid-refresh")
        return record

    def _record_generation_failure(self, playlist_id: int, error_message: str) -> None:
        """Stamp the error on the row WITHOUT touching tracks."""
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE personalized_playlists
                SET last_generation_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error_message[:500], playlist_id),
            )
            conn.commit()

    def _fetch_playlist_row(self, kind: str, variant: str, profile_id: int) -> Optional[PlaylistRecord]:
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, profile_id, kind, variant, name, config_json,
                       track_count, last_generated_at, last_synced_at,
                       last_generation_source, last_generation_error
                FROM personalized_playlists
                WHERE profile_id = ? AND kind = ? AND variant = ?
                """,
                (profile_id, kind, variant),
            )
            row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def _fetch_playlist_row_by_id(self, playlist_id: int) -> Optional[PlaylistRecord]:
        with self.database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, profile_id, kind, variant, name, config_json,
                       track_count, last_generated_at, last_synced_at,
                       last_generation_source, last_generation_error
                FROM personalized_playlists
                WHERE id = ?
                """,
                (playlist_id,),
            )
            row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def _row_to_record(row: Any) -> PlaylistRecord:
        # Tolerate sqlite3.Row + plain tuples.
        if hasattr(row, 'keys'):
            row = dict(row)
            return PlaylistRecord(
                id=row['id'], profile_id=row['profile_id'],
                kind=row['kind'], variant=row['variant'] or '',
                name=row['name'],
                config=PlaylistConfig.from_json_dict(_safe_json_loads(row['config_json'])),
                track_count=row['track_count'] or 0,
                last_generated_at=row.get('last_generated_at'),
                last_synced_at=row.get('last_synced_at'),
                last_generation_source=row.get('last_generation_source'),
                last_generation_error=row.get('last_generation_error'),
            )
        # Tuple form: positional access matches SELECT order above.
        return PlaylistRecord(
            id=row[0], profile_id=row[1],
            kind=row[2], variant=row[3] or '',
            name=row[4],
            config=PlaylistConfig.from_json_dict(_safe_json_loads(row[5])),
            track_count=row[6] or 0,
            last_generated_at=row[7],
            last_synced_at=row[8],
            last_generation_source=row[9],
            last_generation_error=row[10],
        )

    @staticmethod
    def _row_to_track(row: Any) -> Track:
        if hasattr(row, 'keys'):
            row = dict(row)
            return Track(
                spotify_track_id=row.get('spotify_track_id'),
                itunes_track_id=row.get('itunes_track_id'),
                deezer_track_id=row.get('deezer_track_id'),
                track_name=row.get('track_name', ''),
                artist_name=row.get('artist_name', ''),
                album_name=row.get('album_name') or '',
                album_cover_url=row.get('album_cover_url'),
                duration_ms=int(row.get('duration_ms') or 0),
                popularity=int(row.get('popularity') or 0),
                track_data_json=_safe_json_loads(row.get('track_data_json')),
            )
        return Track(
            spotify_track_id=row[0], itunes_track_id=row[1], deezer_track_id=row[2],
            track_name=row[3], artist_name=row[4], album_name=row[5] or '',
            album_cover_url=row[6], duration_ms=int(row[7] or 0),
            popularity=int(row[8] or 0),
            track_data_json=_safe_json_loads(row[9]),
        )


def _safe_json_loads(value: Any) -> Any:
    """Tolerant JSON parse — returns None / dict / passes through
    non-string values. Avoids exceptions on bad rows so the manager
    never breaks on a single corrupt record."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    if not value.strip():
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


__all__ = ['PersonalizedPlaylistManager']
