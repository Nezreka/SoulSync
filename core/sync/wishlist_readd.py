"""Build the wishlist-add payload for a synced track — the SINGLE source of truth
shared by the live sync (core.discovery.sync) and the sync-detail "re-add to
wishlist" action, so a re-add is byte-for-byte the same payload the auto-add used.

Pure — no I/O. The web route supplies the parsed sync entry and calls the wishlist
service; the live sync calls build_original_tracks_map directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


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


def reconstruct_sync_track_data(
    track_results: Optional[List[Dict[str, Any]]],
    tracks: Optional[List[Dict[str, Any]]],
    track_index: int,
) -> Optional[Dict[str, Any]]:
    """Return the wishlist-add ``spotify_track_data`` for re-adding a synced track.

    Resolves the track the SAME way the auto-add did: the normalized tracks_json
    entry (``build_original_tracks_map``), looked up by the track_result's
    ``source_track_id`` — so the payload (full album object + images + album_type +
    total_tracks + artists-as-dicts) is identical to the original auto-add.

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

    # Primary: the exact normalized track the auto-add used.
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


__all__ = ["normalize_wishlist_track", "build_original_tracks_map", "reconstruct_sync_track_data"]
