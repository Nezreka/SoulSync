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
- POST   /api/personalized/playlist/<kind>/activate         — activate + create automation
- PUT    /api/personalized/playlist/<kind>/auto-refresh     — toggle automation enabled
- PUT    /api/personalized/playlist/<kind>/refresh-interval — change automation schedule
- DELETE /api/personalized/playlist/<kind>                  — deactivate (delete playlist + automation)

Auto-refresh is backed by per-playlist rows in the ``automations`` table
(owned_by='auto_playlist').  The handler creates / toggles / deletes those
rows rather than storing scheduling state in config_json.extra.

The handlers themselves are pure functions returning Python dicts so
they're testable without spinning up Flask. The wiring step in
web_server.py wraps them in ``jsonify`` + URL routing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.personalized.manager import PersonalizedPlaylistManager
from core.personalized.specs import PlaylistKindRegistry, get_registry
from core.personalized.types import PlaylistRecord, Track


# ── Automation helpers ────────────────────────────────────────────────

def _find_playlist_automation(engine, kind: str, variant: str, profile_id: int):
    """Find the automation row for a specific playlist, or None."""
    if engine is None:
        return None
    all_auto = engine.db.get_automations_by_action('personalized_pipeline')
    for auto in (all_auto or []):
        if auto.get('profile_id') != profile_id:
            continue
        if auto.get('is_system'):
            continue
        ac = json.loads(auto.get('action_config') or '{}')
        kinds = ac.get('kinds') or []
        if any(k.get('kind') == kind and k.get('variant', '') == (variant or '')
               for k in kinds):
            return auto
    return None


def _interval_to_trigger(interval_hours: int):
    """Map refresh_interval_hours to (trigger_type, trigger_config)."""
    if interval_hours <= 12:
        return 'schedule', {'interval': interval_hours, 'unit': 'hours'}
    if interval_hours <= 48:
        return 'daily_time', {'time': '01:00'}
    return 'weekly_time', {'time': '01:00', 'days': ['mon']}


def _trigger_to_interval(trigger_type: str, trigger_config: dict) -> int:
    """Reverse-map trigger config to approximate refresh_interval_hours."""
    if trigger_type == 'schedule':
        interval = trigger_config.get('interval', 24)
        unit = trigger_config.get('unit', 'hours')
        if unit == 'minutes':
            return max(1, interval // 60)
        if unit == 'days':
            return interval * 24
        return interval
    if trigger_type == 'daily_time':
        return 24
    if trigger_type == 'weekly_time':
        return 168
    return 24


# ── Serialisation ────────────────────────────────────────────────────

def _record_to_dict(record: PlaylistRecord, automation=None) -> Dict[str, Any]:
    extra = record.config.extra or {}
    result = {
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
        'is_stale': record.is_stale,
    }
    if automation is not None:
        result['automation_id'] = automation['id']
        result['auto_refresh'] = bool(automation.get('enabled', 0))
        result['refresh_interval_hours'] = _trigger_to_interval(
            automation.get('trigger_type', 'schedule'),
            json.loads(automation.get('trigger_config') or '{}'),
        )
    else:
        result['automation_id'] = None
        result['auto_refresh'] = False
        result['refresh_interval_hours'] = 24
    return result


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


# ── Read handlers ────────────────────────────────────────────────────

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


def list_playlists(
    manager: PersonalizedPlaylistManager,
    profile_id: int,
    engine=None,
) -> Dict[str, Any]:
    """List every persisted playlist for a profile, enriched with
    automation data (auto_refresh, interval) from the engine."""
    records = manager.list_playlists(profile_id)
    return {
        'success': True,
        'playlists': [
            _record_to_dict(r, _find_playlist_automation(engine, r.kind, r.variant, profile_id))
            for r in records
        ],
    }


def get_playlist_with_tracks(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    engine=None,
) -> Dict[str, Any]:
    """Get the playlist row + its current track snapshot. Auto-creates
    the row from default config if it doesn't exist (so the UI's first-
    paint of an unseen kind works without a separate ensure call)."""
    record = manager.ensure_playlist(kind, variant, profile_id)
    tracks = manager.get_playlist_tracks(record.id)
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
        'tracks': [_track_to_dict(t) for t in tracks],
    }


# ── Write handlers ───────────────────────────────────────────────────

def refresh_playlist(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    config_overrides: Optional[Dict[str, Any]] = None,
    engine=None,
) -> Dict[str, Any]:
    """Run the kind's generator and persist the snapshot. Returns the
    fresh row + tracks."""
    record = manager.refresh_playlist(kind, variant, profile_id, config_overrides=config_overrides)
    tracks = manager.get_playlist_tracks(record.id)
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
        'tracks': [_track_to_dict(t) for t in tracks],
    }


def update_config(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    overrides: Dict[str, Any],
    engine=None,
) -> Dict[str, Any]:
    """Patch the playlist's config with the provided fields."""
    record = manager.update_config(kind, variant, profile_id, overrides)
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
    }


def activate_playlist(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    engine,
    refresh_interval_hours: int = 24,
) -> Dict[str, Any]:
    """Activate a playlist: ensure it exists, create an automation row
    for scheduling, and do an initial refresh."""
    record = manager.ensure_playlist(kind, variant, profile_id)

    existing = _find_playlist_automation(engine, kind, variant, profile_id)
    if existing is None:
        trigger_type, trigger_config = _interval_to_trigger(refresh_interval_hours)
        kind_display = kind.replace('_', ' ').title()
        variant_display = f" ({variant})" if variant else ''
        aid = engine.db.create_automation(
            name=f"Auto-Refresh: {kind_display}{variant_display}",
            trigger_type=trigger_type,
            trigger_config=json.dumps(trigger_config),
            action_type='personalized_pipeline',
            action_config=json.dumps({'kinds': [{'kind': kind, 'variant': variant or ''}]}),
            profile_id=profile_id,
            owned_by='auto_playlist',
        )
        if aid:
            engine.schedule_automation(aid)

    try:
        record = manager.refresh_playlist(kind, variant, profile_id)
    except Exception:  # noqa: BLE001
        logger.debug("Initial refresh after activate failed (will retry on schedule)", exc_info=True)

    tracks = manager.get_playlist_tracks(record.id)
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
        'tracks': [_track_to_dict(t) for t in tracks],
    }


def toggle_auto_refresh(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    engine,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Toggle the automation row's enabled status."""
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    if automation is None:
        record = manager.ensure_playlist(kind, variant, profile_id)
        automation = _find_playlist_automation(engine, kind, variant, profile_id)

    if automation is not None and enabled is not None:
        current = bool(automation.get('enabled', 0))
        if current != bool(enabled):
            engine.db.toggle_automation(automation['id'])
            if enabled:
                engine.schedule_automation(automation['id'])
            automation = _find_playlist_automation(engine, kind, variant, profile_id)

    record = manager.get_playlist(kind, variant, profile_id) or manager.ensure_playlist(kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
    }


def update_refresh_interval(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    engine,
    refresh_interval_hours: int,
) -> Dict[str, Any]:
    """Update the automation's trigger schedule for a playlist."""
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    if automation is None:
        record = manager.ensure_playlist(kind, variant, profile_id)
        automation = _find_playlist_automation(engine, kind, variant, profile_id)

    if automation is not None:
        trigger_type, trigger_config = _interval_to_trigger(refresh_interval_hours)
        engine.db.update_automation(
            automation['id'],
            trigger_type=trigger_type,
            trigger_config=json.dumps(trigger_config),
        )
        engine.schedule_automation(automation['id'])
        automation = _find_playlist_automation(engine, kind, variant, profile_id)

    record = manager.get_playlist(kind, variant, profile_id) or manager.ensure_playlist(kind, variant, profile_id)
    return {
        'success': True,
        'playlist': _record_to_dict(record, automation),
    }


def delete_playlist(
    manager: PersonalizedPlaylistManager,
    kind: str,
    variant: str,
    profile_id: int,
    engine=None,
) -> Dict[str, Any]:
    """Delete a playlist and its associated automation row."""
    automation = _find_playlist_automation(engine, kind, variant, profile_id)
    if automation is not None and engine is not None:
        engine.db.delete_automation(automation['id'])

    deleted = manager.delete_playlist(kind, variant, profile_id)
    if not deleted:
        return {'success': False, 'error': 'Playlist not found'}
    return {'success': True}


__all__ = [
    'list_kinds',
    'list_playlists',
    'get_playlist_with_tracks',
    'refresh_playlist',
    'update_config',
    'activate_playlist',
    'toggle_auto_refresh',
    'update_refresh_interval',
    'delete_playlist',
]
