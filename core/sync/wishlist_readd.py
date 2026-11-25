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
    """Normalized tracks keyed by playlist ID first, then by catalogue ID."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in tracks_json or []:
        if not isinstance(t, dict):
            continue
        # Two playlist versions can share the same catalogue match.
        track_id = t.get("source_track_id") or t.get("id")
        if track_id:
            out[str(track_id)] = normalize_wishlist_track(t)
    return out


def reconstruct_sync_track_data(
    track_results: Optional[List[Dict[str, Any]]],
    tracks: Optional[List[Dict[str, Any]]],
    track_index: int,
) -> Optional[Dict[str, Any]]:
    """Return the wishlist-add ``spotify_track_data`` for re-adding a synced track.

    Uses the same normalized tracks_json entry as auto-add. History stores the
    catalogue ID in ``source_track_id`` and the native ID in ``playlist_track_id``.

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
    # Wing-it fallback stubs have no catalogue release behind them (so no album or
    # cover) — the live sync keeps them off the wishlist unless
    # wishlist.wing_it_guesses is on, and the re-add has to make the SAME call or it
    # would store a coverless placeholder the sync had deliberately skipped.
    if is_stub_id(sid) and not should_wishlist_stub(tr.get('artist'), tr.get('name'), sid):
        return None

    playlist_track_id = str(tr.get("playlist_track_id") or "")
    originals = build_original_tracks_map(tracks)
    if playlist_track_id:
        if payload := originals.get(playlist_track_id):
            return payload
    elif sid:
        # Older history only stored the catalogue ID. Don't guess a native ID
        # when more than one playlist version matched that catalogue track.
        matches = [track for track in originals.values() if str(track.get("id") or "") == sid]
        if len(matches) == 1:
            return matches[0]

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
        **({"source_track_id": playlist_track_id} if playlist_track_id else {}),
        'name': tr.get('name') or '',
        'artists': [{'name': tr.get('artist') or ''}],
        'album': album,
        'duration_ms': tr.get('duration_ms') or 0,
    })


__all__ = ["normalize_wishlist_track", "build_original_tracks_map", "reconstruct_sync_track_data"]
