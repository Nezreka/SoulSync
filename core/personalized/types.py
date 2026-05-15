"""Shared dataclasses for the personalized-playlist subsystem.

These are pure data containers — no business logic, no IO. The
manager + specs + generators all speak in these types so the seam
between them stays mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Track:
    """A track in a personalized playlist.

    Mirrors the shape returned by
    ``PersonalizedPlaylistsService._build_track_dict`` so the legacy
    generators can be wrapped without translating fields. Always at
    least one of the source IDs is populated; ``track_data_json`` is
    the full enriched track object when available (used by sync /
    download paths that need richer metadata than just the ID)."""

    track_name: str
    artist_name: str
    album_name: str = ''
    spotify_track_id: Optional[str] = None
    itunes_track_id: Optional[str] = None
    deezer_track_id: Optional[str] = None
    album_cover_url: Optional[str] = None
    duration_ms: int = 0
    popularity: int = 0
    track_data_json: Optional[Any] = None  # dict OR JSON string OR None
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Track':
        """Coerce a legacy generator's output dict into a Track.

        Tolerates the ``_artist_genres_raw`` / ``_release_date`` extra-
        column passthroughs by ignoring them — this dataclass only
        carries the storage-layer fields."""
        return cls(
            track_name=d.get('track_name', 'Unknown'),
            artist_name=d.get('artist_name', 'Unknown'),
            album_name=d.get('album_name', '') or '',
            spotify_track_id=d.get('spotify_track_id'),
            itunes_track_id=d.get('itunes_track_id'),
            deezer_track_id=d.get('deezer_track_id'),
            album_cover_url=d.get('album_cover_url'),
            duration_ms=int(d.get('duration_ms') or 0),
            popularity=int(d.get('popularity') or 0),
            track_data_json=d.get('track_data_json'),
            source=d.get('source'),
        )

    def primary_id(self) -> Optional[str]:
        """Return the first non-empty source ID. Used as the staleness-
        history key + the dedupe key when persisting tracks."""
        return (
            self.spotify_track_id
            or self.itunes_track_id
            or self.deezer_track_id
            or None
        )


@dataclass
class PlaylistConfig:
    """User-tweakable knobs per playlist instance.

    Stored as JSON in `personalized_playlists.config_json`. Defaults
    come from the kind's spec; user overrides override per-playlist.

    Fields:
      - limit: target number of tracks
      - max_per_album / max_per_artist: diversity caps
      - popularity_min / popularity_max: filter bounds (None = ignore)
      - exclude_recent_days: avoid tracks served by this kind in the
        last N days (0 = no exclusion)
      - recency_days: only include tracks released in the last N days
        (None = all-time)
      - seed: optional deterministic seed for randomization (None =
        use system random; same seed + same pool = same output)
      - extra: free-form per-kind extension dict (e.g. seasonal mix
        stores ``selected_seasons``, time machine stores
        ``selected_decades``, genre stores ``selected_genres``).
    """

    limit: int = 50
    max_per_album: int = 2
    max_per_artist: int = 3
    popularity_min: Optional[int] = None
    popularity_max: Optional[int] = None
    exclude_recent_days: int = 0
    recency_days: Optional[int] = None
    seed: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe dict for storage."""
        return {
            'limit': self.limit,
            'max_per_album': self.max_per_album,
            'max_per_artist': self.max_per_artist,
            'popularity_min': self.popularity_min,
            'popularity_max': self.popularity_max,
            'exclude_recent_days': self.exclude_recent_days,
            'recency_days': self.recency_days,
            'seed': self.seed,
            'extra': dict(self.extra),
        }

    @classmethod
    def from_json_dict(cls, d: Optional[Dict[str, Any]]) -> 'PlaylistConfig':
        """Reconstruct from a stored JSON dict. Missing fields fall
        back to defaults so old rows + new code stay compatible."""
        if not isinstance(d, dict):
            return cls()
        return cls(
            limit=int(d.get('limit', 50)),
            max_per_album=int(d.get('max_per_album', 2)),
            max_per_artist=int(d.get('max_per_artist', 3)),
            popularity_min=d.get('popularity_min'),
            popularity_max=d.get('popularity_max'),
            exclude_recent_days=int(d.get('exclude_recent_days', 0)),
            recency_days=d.get('recency_days'),
            seed=d.get('seed'),
            extra=dict(d.get('extra') or {}),
        )

    def merged(self, overrides: Dict[str, Any]) -> 'PlaylistConfig':
        """Return a new PlaylistConfig with `overrides` merged in.

        Used when a user PATCHes their per-playlist config — apply
        only the fields they sent, leave the rest at their stored
        values."""
        base = self.to_json_dict()
        for key, value in (overrides or {}).items():
            if key == 'extra' and isinstance(value, dict):
                base['extra'] = {**base.get('extra', {}), **value}
            elif key in base:
                base[key] = value
        return PlaylistConfig.from_json_dict(base)


@dataclass
class PlaylistRecord:
    """One row of `personalized_playlists` plus its track count.

    The live track list is fetched separately via
    ``PersonalizedPlaylistManager.get_playlist_tracks(playlist_id)``
    so list / detail responses can stay cheap when the caller only
    needs metadata."""

    id: int
    profile_id: int
    kind: str
    variant: str
    name: str
    config: PlaylistConfig
    track_count: int
    last_generated_at: Optional[str]
    last_synced_at: Optional[str]
    last_generation_source: Optional[str]
    last_generation_error: Optional[str]


__all__ = [
    'Track',
    'PlaylistConfig',
    'PlaylistRecord',
]
