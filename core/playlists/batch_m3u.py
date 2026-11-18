"""The tracks a playlist's m3u is written from when its download batch ends.

A batch only holds what it went out to download. Tracks the library already
had never get queued, so writing the m3u from the batch alone cut a six track
playlist down to the one new download (#2111). A batch that belongs to a
mirrored playlist writes the whole mirror, in playlist order, instead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("playlists.batch_m3u")

_NOT_PLAYLISTS = ('wishlist', 'wishlist_manual')


def _int_pk(value: Any) -> Optional[int]:
    # only a real int is a mirror pk. a digit string is an upstream id
    # (deezer ids are all digits), see post_download_sync
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def batch_mirror_id(batch: Dict[str, Any], db: Any) -> Optional[int]:
    """The mirrored playlist a download batch belongs to, or None.

    by id first (the batch's own mirror pk, then a synthetic mirror sync id
    like auto_mirror_5). the name is only asked for a batch that says it is
    a playlist batch, so an album download never lands on a same-named mirror.
    """
    pk = _int_pk(batch.get('mirrored_playlist_id'))
    if pk:
        return pk
    from core.sync.mirrored_server_link import mirrored_pk_from_sync_id
    for ref in (batch.get('playlist_id'), batch.get('source_playlist_id')):
        pk = mirrored_pk_from_sync_id(ref)
        if pk:
            return pk
    if not (batch.get('is_playlist_batch') or batch.get('organize_by_playlist')
            or batch.get('playlist_folder_mode')):
        return None
    ref = batch.get('mirrored_playlist_id') or batch.get('source_playlist_id') or batch.get('playlist_id')
    if not ref or str(ref) in _NOT_PLAYLISTS:
        return None
    try:
        found = db.resolve_mirrored_playlist_assignment(
            playlist_ref=ref,
            playlist_name=batch.get('source_playlist_name') or batch.get('playlist_name'),
            profile_id=int(batch.get('profile_id') or 1),
        )
    except Exception as exc:  # noqa: BLE001 - falls back to the batch's own tracks
        logger.debug("[M3U] mirror lookup failed for %r: %s", ref, exc)
        return None
    return _int_pk(int(found['id'])) if found and found.get('id') else None


def _m3u_row(track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from core.playlists.organize_download import mirrored_tracks_to_download_json
    # the matched metadata when discovery found one, same as the download used
    converted = mirrored_tracks_to_download_json([track])
    if converted:
        entry = converted[0]
        artists = entry.get('artists') or []
        first = artists[0] if artists else ''
        artist = first.get('name', '') if isinstance(first, dict) else str(first or '')
        name = entry.get('name') or ''
        duration = entry.get('duration_ms') or 0
    else:
        name, artist, duration = '', '', 0
    name = name or (track.get('track_name') or '').strip()
    artist = artist or (track.get('artist_name') or '').strip()
    if not name:
        return None
    return {'name': name, 'artist': artist, 'duration_ms': duration or track.get('duration_ms') or 0}


def playlist_m3u_tracks(batch: Dict[str, Any], batch_tracks: List[Dict[str, Any]],
                        db: Any) -> List[Dict[str, Any]]:
    """``[{name, artist, duration_ms}]`` to write the batch's m3u from: the
    whole mirrored playlist when the batch has one, else the batch's tracks."""
    try:
        pk = batch_mirror_id(batch, db)
        if not pk:
            return batch_tracks
        rows = db.get_mirrored_playlist_tracks(pk, profile_id=int(batch.get('profile_id') or 1))
        full = [r for r in (_m3u_row(t) for t in rows or []) if r]
        if full:
            return full
    except Exception as exc:  # noqa: BLE001 - the batch's own list still writes
        logger.debug("[M3U] full playlist lookup failed: %s", exc)
    return batch_tracks
