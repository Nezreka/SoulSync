"""HTTP endpoint handlers for the personalized-playlists subsystem.

Wired into the Flask app from web_server.py. Each handler is a thin
wrapper that:
1. Pulls profile id + manager from request context.
2. Calls one PersonalizedPlaylistManager method.
3. Returns a JSON-serializable shape.

Live routes (registered against the main Flask app):
- GET    /api/personalized/playlists                       — list
- GET    /api/personalized/kinds                           — registry
- GET    /api/personalized/playlist/<kind>                  — singleton
- GET    /api/personalized/playlist/<kind>/<variant>        — variant
- POST   /api/personalized/playlist/<kind>/refresh          — singleton
- POST   /api/personalized/playlist/<kind>/<variant>/refresh — variant
- PUT    /api/personalized/playlist/<kind>/config           — singleton
- PUT    /api/personalized/playlist/<kind>/<variant>/config  — variant

The handlers themselves are pure functions returning Python dicts so
they're testable without spinning up Flask. The wiring step in
web_server.py wraps them in `jsonify` + URL routing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.personalized.manager import PersonalizedPlaylistManager
from core.personalized.specs import PlaylistKindRegistry, get_registry
from core.personalized.types import PlaylistRecord, Track


def _record_to_dict(record: PlaylistRecord) -> Dict[str, Any]:
    return {
        'id': record.id,
        'profile_id': record.profile_id,
        'kind': record.kind,
        'variant': record.variant,
        'name': record.name,
        'config': record.config.to_json_dict(),
        'track_count': record.track_count,
        'last_generated_at': record.last_generated_at,
        'last_synced_at': record.last_synced_at,
        'last_generation_source': record.last_generation_source,
        'last_generation_error': record.last_generation_error,
    }


def _track_to_dict(track: Track) -> Dict[str, Any]:
    return {
        'spotify_track_id': track.spotify_track_id,
        'itunes_track_id': track.itunes_track_id,
        'deezer_track_id': track.deezer_track_id,
        'track_name': track.track_name,
        'artist_name': track.artist_name,
        'album_name': track.album_name,
        'album_cover_url': track.album_cover_url,
        'duration_ms': track.duration_ms,
        'popularity': track.popularity,
        'track_data_json': track.track_data_json,
        'source': track.source,
    }


def list_kinds(
    registry: Optional[PlaylistKindRegistry] = None,
    manager: Optional[PersonalizedPlaylistManager] = None,
) -> Dict[str, Any]:
    """Return every registered playlist kind with metadata.

    UI uses this to render the "available playlists" picker. Each
    kind reports whether it requires a variant; when a manager is
    supplied AND the kind has a variant_resolver, the resolved
    variant list is also included so the UI can render variant
    checkboxes without a second round-trip per kind."""
    reg = registry or get_registry()
    out = []
    for spec in reg.all():
        entry = {
            'kind': spec.kind,
            'name_template': spec.name_template,
            'description': spec.description,
            'requires_variant': spec.requires_variant,
            'tags': list(spec.tags),
            'default_config': spec.default_config.to_json_dict(),
            'variants': [],
        }
        if manager is not None and spec.variant_resolver is not None:
            try:
                entry['variants'] = list(spec.variant_resolver(manager.deps) or [])
            except Exception:
                entry['variants'] = []
        out.append(entry)
    return {'success': True, 'kinds': out}


def list_playlists(manager: PersonalizedPlaylistManager, profile_id: int) -> Dict[str, Any]:
    """List every persisted playlist for a profile."""
    records = manager.list_playlists(profile_id)
    return {
        'success': True,
        'playlists': [_record_to_dict(r) for r in records],
    }


def get_playlist_with_tracks(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
) -> Dict[str, Any]:
    """Get the playlist row + its current track snapshot. Auto-creates
    the row from default config if it doesn't exist (so the UI's first-
    paint of an unseen kind works without a separate ensure call)."""
    record = manager.ensure_playlist(kind, variant, profile_id)
    tracks = manager.get_playlist_tracks(record.id)
    return {
        'success': True,
        'playlist': _record_to_dict(record),
        'tracks': [_track_to_dict(t) for t in tracks],
    }


def refresh_playlist(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the kind's generator and persist the snapshot. Returns the
    fresh row + tracks."""
    record = manager.refresh_playlist(kind, variant, profile_id, config_overrides=config_overrides)
    tracks = manager.get_playlist_tracks(record.id)
    return {
        'success': True,
        'playlist': _record_to_dict(record),
        'tracks': [_track_to_dict(t) for t in tracks],
    }


def update_config(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """Patch the playlist's config with the provided fields."""
    record = manager.update_config(kind, variant, profile_id, overrides)
    return {
        'success': True,
        'playlist': _record_to_dict(record),
    }


__all__ = [
    'list_kinds',
    'list_playlists',
    'get_playlist_with_tracks',
    'refresh_playlist',
    'update_config',
]
