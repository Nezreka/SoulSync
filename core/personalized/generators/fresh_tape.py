"""Fresh Tape (Spotify Release Radar) generator.

Reads the curated track-id list cached in ``discovery_curated_playlists``
under ``release_radar_<source>`` (with fallback to ``release_radar``)
and hydrates each ID against the discovery pool to produce full Track
records. The Spotify enrichment worker is responsible for keeping the
curated list fresh — this generator is just a read-and-hydrate path."""

from __future__ import annotations

import json
from typing import Any, List

from core.personalized.generators._common import coerce_tracks
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'fresh_tape'


def _hydrate_curated(deps: Any, curated_type_prefix: str, config: PlaylistConfig) -> List[Track]:
    """Shared body for Fresh Tape + Archives — pulls the cached IDs
    from discovery_curated_playlists and hydrates them via the live
    discovery pool. Returns a Track list trimmed to ``config.limit``."""
    # Allow tests to inject a fake db / service; production flow gets
    # them from the manager's deps.
    db = getattr(deps, 'database', None) or (deps.get('database') if isinstance(deps, dict) else None)
    if db is None:
        raise RuntimeError("Curated-playlist generator deps missing `database`")

    profile_id = _resolve_profile_id(deps)
    active_source = _resolve_active_source(deps)

    # Try source-specific then generic, mirrors web_server endpoint behavior.
    curated_ids = (
        db.get_curated_playlist(f'{curated_type_prefix}_{active_source}', profile_id=profile_id)
        or db.get_curated_playlist(curated_type_prefix, profile_id=profile_id)
        or []
    )
    if not curated_ids:
        return []

    pool_rows = db.get_discovery_pool_tracks(
        limit=5000, new_releases_only=False,
        source=active_source, profile_id=profile_id,
    )
    by_id = {}
    for t in pool_rows:
        if active_source == 'spotify' and getattr(t, 'spotify_track_id', None):
            by_id[t.spotify_track_id] = t
        elif active_source == 'deezer' and getattr(t, 'deezer_track_id', None):
            by_id[t.deezer_track_id] = t
        elif active_source == 'itunes' and getattr(t, 'itunes_track_id', None):
            by_id[t.itunes_track_id] = t

    tracks: List[Track] = []
    for tid in curated_ids:
        candidate = by_id.get(tid)
        if candidate is None:
            continue
        # The pool track is a row-like object; coerce to dict for
        # Track.from_dict's existing tolerance.
        td = getattr(candidate, 'track_data_json', None)
        if isinstance(td, str):
            try:
                td = json.loads(td)
            except (ValueError, TypeError):
                td = None
        track_dict = {
            'spotify_track_id': getattr(candidate, 'spotify_track_id', None),
            'itunes_track_id': getattr(candidate, 'itunes_track_id', None),
            'deezer_track_id': getattr(candidate, 'deezer_track_id', None),
            'track_name': getattr(candidate, 'track_name', ''),
            'artist_name': getattr(candidate, 'artist_name', ''),
            'album_name': getattr(candidate, 'album_name', ''),
            'album_cover_url': getattr(candidate, 'album_cover_url', None),
            'duration_ms': getattr(candidate, 'duration_ms', 0),
            'popularity': getattr(candidate, 'popularity', 0),
            'track_data_json': td,
            'source': getattr(candidate, 'source', active_source),
        }
        tracks.append(Track.from_dict(track_dict))
        if len(tracks) >= config.limit:
            break
    return tracks


def _resolve_profile_id(deps: Any) -> int:
    fn = getattr(deps, 'get_current_profile_id', None) or (
        deps.get('get_current_profile_id') if isinstance(deps, dict) else None
    )
    return fn() if callable(fn) else 1


def _resolve_active_source(deps: Any) -> str:
    fn = getattr(deps, 'get_active_discovery_source', None) or (
        deps.get('get_active_discovery_source') if isinstance(deps, dict) else None
    )
    return fn() if callable(fn) else 'spotify'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    return _hydrate_curated(deps, 'release_radar', config)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Fresh Tape',
    description='Your Spotify Release Radar — new releases from artists you follow.',
    default_config=PlaylistConfig(limit=50, max_per_album=5, max_per_artist=10),
    generator=generate,
    requires_variant=False,
    tags=['curated', 'spotify'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
