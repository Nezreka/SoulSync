"""Download missing tracks into playlist-folder layout for mirrored playlists."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def mirrored_tracks_to_download_json(tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert mirrored playlist rows to the payload expected by the download master."""
    out: List[Dict[str, Any]] = []
    for t in tracks:
        extra = {}
        if t.get('extra_data'):
            try:
                extra = json.loads(t['extra_data']) if isinstance(t['extra_data'], str) else t['extra_data']
            except (json.JSONDecodeError, TypeError):
                extra = {}

        if extra.get('discovered') and extra.get('matched_data'):
            md = extra['matched_data']
            album_raw = md.get('album', '')
            album_obj = album_raw if isinstance(album_raw, dict) else {'name': album_raw or ''}
            entry = {
                'name': md.get('name', ''),
                'artists': md.get('artists', [{'name': t.get('artist_name', '')}]),
                'album': album_obj,
                'duration_ms': md.get('duration_ms', 0),
                'id': md.get('id', ''),
            }
            if md.get('track_number'):
                entry['track_number'] = md['track_number']
            if md.get('disc_number'):
                entry['disc_number'] = md['disc_number']
            out.append(entry)
            continue

        hint = extra.get('spotify_hint', {})
        track_image = (t.get('image_url') or '').strip()
        album_obj = {
            'name': (t.get('album_name') or '').strip(),
            'images': [{'url': track_image, 'height': 300, 'width': 300}] if track_image else [],
        }

        if hint.get('id') and hint.get('name'):
            hint_artists = hint.get('artists', [])
            if hint_artists and isinstance(hint_artists[0], str):
                hint_artists = [{'name': a} for a in hint_artists]
            elif not hint_artists:
                hint_artists = [{'name': t.get('artist_name', '')}]
            out.append({
                'name': hint['name'],
                'artists': hint_artists,
                'album': album_obj,
                'duration_ms': t.get('duration_ms', 0),
                'id': hint['id'],
            })
        elif t.get('source_track_id') and (t.get('track_name') or '').strip():
            out.append({
                'name': t['track_name'].strip(),
                'artists': [{'name': (t.get('artist_name') or '').strip() or 'Unknown Artist'}],
                'album': album_obj,
                'duration_ms': t.get('duration_ms', 0),
                'id': t['source_track_id'],
            })
    return out


def run_playlist_organize_download(
    deps: Any,
    *,
    mirrored_playlist_id: int,
    profile_id: int = 1,
    get_batch_max_concurrent: Callable[[bool], int],
    run_full_missing_tracks_process: Callable[..., Any],
    record_sync_history_start: Optional[Callable[..., Any]] = None,
    detect_sync_source: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Queue a playlist-folder missing-tracks batch for one mirrored playlist."""
    db = deps.get_database()
    pl = db.get_mirrored_playlist(int(mirrored_playlist_id))
    if not pl:
        return {'status': 'error', 'reason': 'Playlist not found'}

    source_playlist_ref = (pl.get('source_playlist_id') or '').strip()
    source = (pl.get('source') or 'spotify').strip() or 'spotify'

    tracks = db.get_mirrored_playlist_tracks(int(mirrored_playlist_id))
    tracks_json = mirrored_tracks_to_download_json(tracks)
    if not tracks_json:
        return {'status': 'skipped', 'reason': 'No processable tracks'}

    batch_id = str(uuid.uuid4())
    playlist_id = str(mirrored_playlist_id)
    playlist_name = pl.get('name', 'Unknown Playlist')

    download_batches = deps.get_download_batches()
    tasks_lock = deps.tasks_lock

    with tasks_lock:
        active_analysis = sum(
            1 for batch in download_batches.values() if batch.get('phase') == 'analysis'
        )
        if active_analysis >= 3:
            return {'status': 'error', 'reason': 'Too many analysis processes running'}

        download_batches[batch_id] = {
            'phase': 'analysis',
            'playlist_id': playlist_id,
            'playlist_name': playlist_name,
            'queue': [],
            'active_count': 0,
            'max_concurrent': get_batch_max_concurrent(False),
            'permanently_failed_tracks': [],
            'cancelled_tracks': set(),
            'queue_index': 0,
            'analysis_total': len(tracks_json),
            'profile_id': profile_id,
            'analysis_processed': 0,
            'analysis_results': [],
            'force_download_all': False,
            'ignore_manual_matches': False,
            'playlist_folder_mode': True,
            'is_album_download': False,
            'album_context': None,
            'artist_context': None,
            'wing_it': False,
            'batch_source': source,
            'auto_initiated': False,
            'organize_by_playlist': True,
            'source_playlist_ref': source_playlist_ref,
            'mirrored_playlist_id': int(mirrored_playlist_id),
        }

    if record_sync_history_start:
        try:
            record_sync_history_start(
                batch_id,
                playlist_id,
                playlist_name,
                tracks_json,
                False,
                None,
                None,
                True,
                source_page='automation',
            )
        except Exception as hist_err:
            logger.debug("organize download sync history: %s", hist_err)

    try:
        deps.missing_download_executor.submit(
            run_full_missing_tracks_process,
            batch_id,
            playlist_id,
            tracks_json,
        )
    except Exception as submit_err:
        # Don't leave the batch stranded in 'analysis' holding one of the limited
        # analysis slots if the executor refuses the job.
        logger.error("[Organize Download] Failed to submit batch %s: %s", batch_id, submit_err)
        with tasks_lock:
            download_batches.pop(batch_id, None)
        return {'status': 'error', 'reason': f'submit failed: {submit_err}'}
    logger.info(
        "[Organize Download] Started batch %s for mirrored playlist %s (%s tracks)",
        batch_id,
        playlist_name,
        len(tracks_json),
    )
    return {
        'status': 'started',
        'batch_id': batch_id,
        'track_count': len(tracks_json),
    }


__all__ = ['mirrored_tracks_to_download_json', 'run_playlist_organize_download']
