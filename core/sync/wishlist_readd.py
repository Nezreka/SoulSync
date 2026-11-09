"""Build the wishlist-add payload for a synced track — the SINGLE source of truth
shared by the live sync (core.discovery.sync) and the sync-detail "re-add to
wishlist" action, so a re-add is byte-for-byte the same payload the auto-add used.

Pure — no I/O. The web route supplies the parsed sync entry and calls the wishlist
service; the live sync calls build_original_tracks_map directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.discovery.wing_it import is_stub_id, should_wishlist_stub


def normalize_wishlist_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize ONE tracks_json track into the wishlist-add shape: album coerced to
    a dict (preserving images + album_type/total_tracks/release_date), artists to a
    list of dicts. Copy-safe — never mutates the input."""
    normalized = dict(track)
    raw_album = normalized.get('album', '')
    if isinstance(raw_album, dict):
        album = dict(raw_album)
        album.setdefault('name', 'Unknown Album')
        album.setdefault('images', [])
        normalized['album'] = album
    else:
        name = raw_album if isinstance(raw_album, str) else (str(raw_album) if raw_album else '')
        normalized['album'] = {
            'name': name or normalized.get('name', 'Unknown Album'),
            'images': [], 'album_type': 'single', 'total_tracks': 1, 'release_date': '',
        }
    raw_artists = normalized.get('artists', [])
    if raw_artists and isinstance(raw_artists[0], str):
        normalized['artists'] = [{'name': a} for a in raw_artists]
    return normalized


def build_original_tracks_map(tracks_json: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """``{track_id: normalized_track}`` for a sync's tracks_json — the full-fidelity
    map the live sync builds before auto-wishlisting unmatched tracks. One source of
    truth so the re-add matches the auto-add exactly."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in tracks_json or []:
        if not isinstance(t, dict):
            continue
        track_id = t.get('id', '')
        if track_id:
            out[str(track_id)] = normalize_wishlist_track(t)
    return out


def build_original_tracks_by_source(
    tracks_json: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Original payloads keyed by mirrored source identity, not provider ID."""
    out: Dict[str, Dict[str, Any]] = {}
    for track in tracks_json or []:
        if not isinstance(track, dict):
            continue
        source_track_id = str(track.get('source_track_id') or '').strip()
        if source_track_id:
            out[source_track_id] = normalize_wishlist_track(track)
    return out


def _mirrored_source_track_id(
    track_result: Dict[str, Any],
    tracks: Optional[List[Dict[str, Any]]],
) -> str:
    """Return verified source identity without treating provider IDs as source IDs."""
    direct = str(track_result.get('mirrored_source_track_id') or '').strip()
    if direct:
        return direct
    try:
        index = int(track_result.get('index'))
        if index < 0:
            return ''
        indexed = (tracks or [])[index]
    except (TypeError, ValueError, IndexError):
        return ''
    if not isinstance(indexed, dict):
        return ''
    provider_track_id = str(track_result.get('source_track_id') or '').strip()
    indexed_provider_id = str(indexed.get('id') or '').strip()
    if provider_track_id and indexed_provider_id != provider_track_id:
        return ''
    return str(indexed.get('source_track_id') or '').strip()


def reconstruct_sync_track_data(
    track_results: Optional[List[Dict[str, Any]]],
    tracks: Optional[List[Dict[str, Any]]],
    track_index: int,
) -> Optional[Dict[str, Any]]:
    """Return the wishlist-add ``spotify_track_data`` for re-adding a synced track.

    Resolves the track the SAME way the auto-add did: source-specific mirrored
    identity first, then the legacy provider-ID map. This keeps colliding provider
    IDs separate while preserving the payload (full album object + images +
    album_type + total_tracks + artists-as-dicts) used by older history rows.

    Only 'wishlist' rows are eligible. Falls back to a normalized rebuild from the
    track_result's own fields (with the album cover from its image_url) when the
    cached track is missing. None when ineligible or unidentifiable.
    """
    if not track_results or track_index < 0 or track_index >= len(track_results):
        return None
    tr = track_results[track_index] or {}
    if tr.get('download_status') != 'wishlist':
        return None

    sid = str(tr.get('source_track_id') or '')
    mirrored_sid = _mirrored_source_track_id(tr, tracks)
    # Wing-it fallback stubs have no catalogue release behind them (so no album or
    # cover) — the live sync keeps them off the wishlist unless
    # wishlist.wing_it_guesses is on, and the re-add has to make the SAME call or it
    # would store a coverless placeholder the sync had deliberately skipped.
    if is_stub_id(sid) and not should_wishlist_stub(tr.get('artist'), tr.get('name')):
        return None

    # Primary: the exact normalized track the auto-add used.
    if mirrored_sid:
        payload = build_original_tracks_by_source(tracks).get(mirrored_sid)
        if payload:
            return payload
    if sid:
        payload = build_original_tracks_map(tracks).get(sid)
        if payload:
            return payload

    # Fallback: rebuild from the track_result fields, through the SAME normalizer so
    # the shape matches, and seed the album cover from the row's image_url.
    if not sid:
        return None
    album: Dict[str, Any] = {'name': tr.get('album') or '', 'album_type': 'single',
                             'total_tracks': 1, 'release_date': ''}
    if tr.get('image_url'):
        album['images'] = [{'url': tr['image_url']}]
    return normalize_wishlist_track({
        'id': sid,
        'name': tr.get('name') or '',
        'artists': [{'name': tr.get('artist') or ''}],
        'album': album,
        'duration_ms': tr.get('duration_ms') or 0,
    })


def sync_history_curation_origin(
    entry: Dict[str, Any],
    track_results: Optional[List[Dict[str, Any]]],
    track_index: int,
    tracks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Recover durable mirrored provenance for a sync-history wishlist retry.

    Only synthetic mirror batch identifiers carry a mirrored-playlist primary
    key. Bare numeric playlist identifiers can be upstream Deezer IDs, so they
    deliberately remain legacy provenance instead of being guessed as mirrors.
    """
    playlist_ref = str((entry or {}).get('playlist_id') or '').strip()
    if not playlist_ref.startswith(('auto_mirror_', 'mirrored_', 'youtube_mirrored_')):
        return None
    if not track_results or track_index < 0 or track_index >= len(track_results):
        return None
    track_result = track_results[track_index] or {}
    source_track_id = _mirrored_source_track_id(track_result, tracks)
    if not source_track_id:
        return None
    from core.playlists.source_refs import extract_mirrored_pk
    mirrored_playlist_id = extract_mirrored_pk(playlist_ref)
    if mirrored_playlist_id is None:
        return None
    return {
        'mirrored_playlist_id': mirrored_playlist_id,
        'source_track_id': source_track_id,
    }


__all__ = [
    "normalize_wishlist_track",
    "build_original_tracks_map",
    "build_original_tracks_by_source",
    "reconstruct_sync_track_data",
    "sync_history_curation_origin",
]
