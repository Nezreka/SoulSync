"""Seasonal Mix generator (variant = season key).

Variant = season key from ``SEASONAL_CONFIG`` (``'halloween'`` /
``'christmas'`` / ``'valentines'`` / ``'summer'`` / ``'spring'`` /
``'autumn'``). One playlist per season — user picks which seasons
to enable; idle seasons can stay un-refreshed until their active
period.

Reads curated track IDs from ``curated_seasonal_playlists`` (via
``SeasonalDiscoveryService.get_curated_seasonal_playlist``) and
hydrates them against ``seasonal_tracks`` (which carries full
metadata including ``track_data_json`` for sync-ready downstream
use)."""

from __future__ import annotations

import json
from typing import Any, List

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'seasonal_mix'


def _resolve_seasonal_service(deps: Any):
    """Pull the SeasonalDiscoveryService instance from deps."""
    svc = getattr(deps, 'seasonal_service', None) or (
        deps.get('seasonal_service') if isinstance(deps, dict) else None
    )
    if svc is None:
        raise RuntimeError(
            "Seasonal mix generator deps missing `seasonal_service` "
            "(SeasonalDiscoveryService instance)."
        )
    return svc


def _resolve_database(deps: Any):
    db = getattr(deps, 'database', None) or (
        deps.get('database') if isinstance(deps, dict) else None
    )
    if db is None:
        raise RuntimeError("Seasonal mix generator deps missing `database`")
    return db


def _resolve_active_source(deps: Any) -> str:
    fn = getattr(deps, 'get_active_discovery_source', None) or (
        deps.get('get_active_discovery_source') if isinstance(deps, dict) else None
    )
    return fn() if callable(fn) else 'spotify'


def _hydrate_seasonal_tracks(db, season_key: str, source: str, track_ids: List[str]) -> List[Track]:
    """Look up the seasonal_tracks rows for the given IDs."""
    if not track_ids:
        return []
    placeholders = ','.join('?' * len(track_ids))
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT spotify_track_id, track_name, artist_name, album_name,
                   album_cover_url, duration_ms, popularity, track_data_json
            FROM seasonal_tracks
            WHERE season_key = ? AND source = ?
              AND spotify_track_id IN ({placeholders})
            """,
            (season_key, source, *track_ids),
        )
        rows = cursor.fetchall()

    by_id = {}
    for r in rows:
        if hasattr(r, 'keys'):
            r = dict(r)
        else:
            r = dict(zip(
                ('spotify_track_id', 'track_name', 'artist_name', 'album_name',
                 'album_cover_url', 'duration_ms', 'popularity', 'track_data_json'),
                r,
            ))
        td = r.get('track_data_json')
        if isinstance(td, str):
            try:
                td = json.loads(td)
            except (ValueError, TypeError):
                td = None
        r['track_data_json'] = td
        r['source'] = source
        by_id[r['spotify_track_id']] = r

    # Preserve curated order.
    return [
        Track.from_dict(by_id[tid])
        for tid in track_ids
        if tid in by_id
    ]


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    if not variant:
        raise ValueError('Seasonal Mix requires a season variant')
    seasonal_service = _resolve_seasonal_service(deps)
    db = _resolve_database(deps)
    source = _resolve_active_source(deps)
    track_ids = seasonal_service.get_curated_seasonal_playlist(variant, source=source) or []
    tracks = _hydrate_seasonal_tracks(db, variant, source, track_ids)
    return tracks[:config.limit]


def variant_resolver(deps: Any) -> List[str]:
    """Return every season key from SEASONAL_CONFIG."""
    try:
        from core.seasonal_discovery import SEASONAL_CONFIG
    except Exception:
        return []
    return list(SEASONAL_CONFIG.keys())


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Seasonal — {variant}',
    description='Holiday / season-themed picks. One playlist per season; user enables which to track.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=3),
    generator=generate,
    variant_resolver=variant_resolver,
    requires_variant=True,
    tags=['curated', 'seasonal'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
