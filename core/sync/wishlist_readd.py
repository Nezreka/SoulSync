"""Rebuild the track data the sync used to auto-wishlist an unmatched track, so the
sync-detail modal can re-add it with the EXACT same context (source_type='playlist'
+ the playlist's name/id). Pure — no I/O; the web route supplies the parsed sync
entry and calls the wishlist service.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def reconstruct_sync_track_data(
    track_results: Optional[List[Dict[str, Any]]],
    tracks: Optional[List[Dict[str, Any]]],
    track_index: int,
) -> Optional[Dict[str, Any]]:
    """Return the ``spotify_track_data`` dict to re-add a synced track to the wishlist.

    Prefers the FULL original track from the cached playlist tracks (``tracks``, i.e.
    tracks_json) — matched by the track_result's ``source_track_id``, then by index —
    because that carries the full album object + images the original auto-add used.
    Falls back to a minimal dict rebuilt from the track_result's own fields.

    Returns None when the index is out of range, the row isn't a 'wishlist'
    (unmatched, auto-added) track, or there's no id to key on — so a caller can't
    re-wishlist a matched/downloaded track or an unidentifiable one.
    """
    if not track_results or track_index < 0 or track_index >= len(track_results):
        return None
    tr = track_results[track_index] or {}
    # Only rows the sync actually sent to the wishlist are re-addable.
    if tr.get('download_status') != 'wishlist':
        return None

    sid = str(tr.get('source_track_id') or '')
    tracks = tracks or []

    # Prefer the full original track: same index if its id matches, else search by id.
    full = None
    if 0 <= track_index < len(tracks):
        cand = tracks[track_index]
        if isinstance(cand, dict) and (not sid or str(cand.get('id') or '') == sid):
            full = cand
    if full is None and sid:
        full = next(
            (t for t in tracks if isinstance(t, dict) and str(t.get('id') or '') == sid),
            None,
        )
    if isinstance(full, dict) and full.get('id'):
        return full

    # Fallback: rebuild from the track_result fields (id required).
    if not sid:
        return None
    album: Dict[str, Any] = {'name': tr.get('album') or ''}
    if tr.get('image_url'):
        album['images'] = [{'url': tr['image_url']}]
    return {
        'id': sid,
        'name': tr.get('name') or '',
        'artists': [{'name': tr.get('artist') or ''}],
        'album': album,
        'duration_ms': tr.get('duration_ms') or 0,
    }


__all__ = ["reconstruct_sync_track_data"]
