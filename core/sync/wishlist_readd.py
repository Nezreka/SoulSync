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

    # Base: the FULL original track (best fidelity — full album object, ids, source,
    # artists) by index if its id matches, else by id search. Copied so we never
    # mutate the caller's tracks list.
    base: Optional[Dict[str, Any]] = None
    if 0 <= track_index < len(tracks):
        cand = tracks[track_index]
        if isinstance(cand, dict) and (not sid or str(cand.get('id') or '') == sid):
            base = dict(cand)
    if base is None and sid:
        match = next(
            (t for t in tracks if isinstance(t, dict) and str(t.get('id') or '') == sid),
            None,
        )
        if match is not None:
            base = dict(match)

    # No full track in the cache — rebuild a minimal dict from the track_result.
    if not (isinstance(base, dict) and base.get('id')):
        if not sid:
            return None
        base = {
            'id': sid,
            'name': tr.get('name') or '',
            'artists': [{'name': tr.get('artist') or ''}],
            'album': {'name': tr.get('album') or ''},
            'duration_ms': tr.get('duration_ms') or 0,
        }

    # Ensure the cover carries through. The wishlist display reads
    # spotify_data.album.images; tracks_json is sometimes stored WITHOUT images, so
    # backfill from the track_result's image_url (the album art the sync extracted)
    # whenever the album has none. This is what makes the re-add reach full parity
    # with the original auto-add's appearance.
    album = base.get('album')
    if not isinstance(album, dict):
        album = {'name': base.get('name', '')}
    else:
        album = dict(album)
    img = tr.get('image_url')
    if img and not album.get('images'):
        album['images'] = [{'url': img}]
    base['album'] = album
    return base


__all__ = ["reconstruct_sync_track_data"]
