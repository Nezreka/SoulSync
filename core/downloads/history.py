"""Sync history recording.

Two write paths: `record_sync_history_start` runs when a batch is
submitted (creates or updates a sync_history row), and
`record_sync_history_completion` runs when a batch finishes (updates
counts + per-track results). Plus `detect_sync_source` which derives
the source label from the playlist_id prefix.

Every write is wrapped in a try/except — sync history is best-effort,
a failure here must never break a real download.
"""

from __future__ import annotations

import json
import logging

from core.runtime_state import download_tasks

logger = logging.getLogger(__name__)


_SOURCE_PREFIX_MAP = [
    # Mirrored playlists go through YouTube discovery, so youtube_mirrored_ must be checked first
    ('auto_mirror_', 'mirrored'), ('youtube_mirrored_', 'mirrored'),
    ('youtube_', 'youtube'), ('beatport_', 'beatport'),
    ('tidal_', 'tidal'), ('deezer_', 'deezer'), ('listenbrainz_', 'listenbrainz'),
    ('spotify_public_', 'spotify_public'), ('discover_album_', 'discover'),
    ('seasonal_album_', 'discover'), ('library_redownload_', 'library'),
    ('issue_download_', 'library'), ('artist_album_', 'spotify'),
    ('enhanced_search_', 'spotify'), ('spotify_library_', 'spotify'),
    ('beatport_release_', 'beatport'), ('beatport_chart_', 'beatport'),
    ('beatport_top100_', 'beatport'), ('beatport_hype100_', 'beatport'),
    ('beatport_sync_', 'beatport'),
]


def detect_sync_source(playlist_id: str) -> str:
    """Derive the sync source from the playlist_id prefix."""
    for prefix, source in _SOURCE_PREFIX_MAP:
        if playlist_id.startswith(prefix):
            return source
    if playlist_id == 'wishlist':
        return 'wishlist'
    return 'spotify'


def record_sync_history_start(
    database,
    batch_id: str,
    playlist_id: str,
    playlist_name: str,
    tracks: list,
    is_album_download: bool,
    album_context,
    artist_context,
    playlist_folder_mode: bool,
    source_page=None,
) -> None:
    """Record a sync start to the database.

    If a previous sync_history row exists for the same playlist_id, update
    it in place rather than creating a duplicate.
    """
    try:
        source = detect_sync_source(playlist_id)
        if playlist_id == 'wishlist':
            sync_type = 'wishlist'
        elif is_album_download:
            sync_type = 'album'
        else:
            sync_type = 'playlist'

        # Extract thumb URL from album context or first track
        thumb_url = None
        if album_context:
            images = album_context.get('images', [])
            if images and isinstance(images, list) and len(images) > 0:
                thumb_url = images[0].get('url') if isinstance(images[0], dict) else images[0]
            if not thumb_url:
                thumb_url = album_context.get('image_url')
        if not thumb_url and tracks:
            first_album = tracks[0].get('album', {})
            if isinstance(first_album, dict):
                imgs = first_album.get('images', [])
                if imgs and isinstance(imgs, list) and len(imgs) > 0:
                    thumb_url = imgs[0].get('url') if isinstance(imgs[0], dict) else imgs[0]

        # Check for existing entry with same playlist_id — update instead of duplicating
        existing = database.get_latest_sync_history_by_playlist(playlist_id)
        if existing:
            try:
                conn = database._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE sync_history
                    SET batch_id = ?, playlist_name = ?, source = ?, sync_type = ?,
                        tracks_json = ?, artist_context = ?, album_context = ?,
                        thumb_url = ?, total_tracks = ?, is_album_download = ?,
                        playlist_folder_mode = ?, source_page = ?, started_at = CURRENT_TIMESTAMP,
                        completed_at = NULL, tracks_found = 0, tracks_downloaded = 0, tracks_failed = 0
                    WHERE id = ?
                    """,
                    (batch_id, playlist_name, source, sync_type,
                     json.dumps(tracks, ensure_ascii=False),
                     json.dumps(artist_context, ensure_ascii=False) if artist_context else None,
                     json.dumps(album_context, ensure_ascii=False) if album_context else None,
                     thumb_url, len(tracks), int(is_album_download), int(playlist_folder_mode),
                     source_page, existing['id']),
                )
                conn.commit()
                logger.info(f"Updated existing sync history entry {existing['id']} for '{playlist_name}'")
                return
            except Exception as e:
                logger.warning(f"Failed to update existing sync history, creating new: {e}")

        database.add_sync_history_entry(
            batch_id=batch_id,
            playlist_id=playlist_id,
            playlist_name=playlist_name,
            source=source,
            sync_type=sync_type,
            tracks_json=json.dumps(tracks, ensure_ascii=False),
            artist_context=json.dumps(artist_context, ensure_ascii=False) if artist_context else None,
            album_context=json.dumps(album_context, ensure_ascii=False) if album_context else None,
            thumb_url=thumb_url,
            total_tracks=len(tracks),
            is_album_download=is_album_download,
            playlist_folder_mode=playlist_folder_mode,
            source_page=source_page,
        )
    except Exception as e:
        logger.warning(f"Failed to record sync history start: {e}")


def record_sync_history_completion(database, batch_id: str, batch: dict) -> None:
    """Update sync_history with completion stats + per-track results.

    NOTE: Called from within tasks_lock context — does NOT acquire it here.
    Reads from `download_tasks` (also lock-protected by caller).
    """
    try:
        analysis_results = batch.get('analysis_results', [])
        tracks_found = sum(1 for r in analysis_results if r.get('found'))
        queue = batch.get('queue', [])
        completed_count = 0
        failed_count = len(batch.get('permanently_failed_tracks', []))

        # Build download status map: track_index → status
        download_status_map: dict = {}
        for task_id in queue:
            task = download_tasks.get(task_id, {})
            ti = task.get('track_index')
            if ti is not None:
                download_status_map[ti] = task.get('status', 'unknown')
            if task.get('status') == 'completed':
                completed_count += 1

        # Build per-track results from analysis
        track_results = []
        for res in analysis_results:
            track_data = res.get('track', {})
            artists = track_data.get('artists', [])
            if artists:
                first = artists[0]
                artist_name = first.get('name', first) if isinstance(first, dict) else str(first)
            else:
                artist_name = ''

            album = track_data.get('album', '')
            album_name = album.get('name', '') if isinstance(album, dict) else str(album or '')

            # Extract image URL
            image_url = ''
            album_obj = track_data.get('album', {})
            if isinstance(album_obj, dict):
                imgs = album_obj.get('images', [])
                if imgs and isinstance(imgs, list) and len(imgs) > 0:
                    image_url = imgs[0].get('url', '') if isinstance(imgs[0], dict) else ''

            idx = res.get('track_index', 0)
            entry = {
                'index': idx,
                'name': track_data.get('name', ''),
                'artist': artist_name,
                'album': album_name,
                'image_url': image_url,
                'duration_ms': track_data.get('duration_ms', 0),
                'source_track_id': track_data.get('id', ''),
                'status': 'found' if res.get('found') else 'not_found',
                'confidence': round(res.get('confidence', 0.0), 3),
                'matched_track': None,
                'download_status': download_status_map.get(idx),
            }
            track_results.append(entry)

        database.update_sync_history_completion(batch_id, tracks_found, completed_count, failed_count)

        if track_results:
            database.update_sync_history_track_results(batch_id, json.dumps(track_results))

    except Exception as e:
        logger.warning(f"Failed to record sync history completion: {e}")
