"""Track redownload endpoint — lifted from web_server.py.

Body is byte-identical to the original. The ``spotify_client`` proxy
+ helper shims for the iTunes/Deezer registry clients let the body
resolve its original names; ``_resolve_library_file_path``,
``_attempt_download_with_candidates``, and ``missing_download_executor``
are injected via init() because they live in web_server.py.
"""
import logging
import time

from flask import jsonify, request

from core.runtime_state import (
    download_batches,
    download_tasks,
    tasks_lock,
)
from core.metadata.registry import (
    get_deezer_client,
    get_itunes_client,
    get_spotify_client,
)
from database.music_database import get_database

logger = logging.getLogger(__name__)


def _get_itunes_client():
    """Mirror of web_server._get_itunes_client — delegates to registry."""
    return get_itunes_client()


def _get_deezer_client():
    """Mirror of web_server._get_deezer_client — delegates to registry."""
    return get_deezer_client()


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily through core.metadata.registry."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


spotify_client = _SpotifyClientProxy()


# Injected at runtime via init().
_resolve_library_file_path = None
_attempt_download_with_candidates = None
missing_download_executor = None


def init(resolve_library_file_path_fn, attempt_download_with_candidates_fn, executor):
    """Bind shared helpers from web_server."""
    global _resolve_library_file_path, _attempt_download_with_candidates
    global missing_download_executor
    _resolve_library_file_path = resolve_library_file_path_fn
    _attempt_download_with_candidates = attempt_download_with_candidates_fn
    missing_download_executor = executor


def redownload_start(track_id):
    """Start downloading a specific track from a selected source to replace the current file."""
    try:
        data = request.get_json()
        metadata = data.get('metadata', {})
        candidate = data.get('candidate', {})
        delete_old = data.get('delete_old_file', True)

        if not candidate.get('username') or not candidate.get('filename'):
            return jsonify({"success": False, "error": "candidate with username and filename required"}), 400

        # Get current track info for old file path
        database = get_database()
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM tracks WHERE id = ?", (track_id,))
        row = cursor.fetchone()
        conn.close()

        old_file_path = None
        if row and row['file_path'] and delete_old:
            old_file_path = _resolve_library_file_path(row['file_path'])

        task_id = f"redownload_{track_id}_{int(time.time())}"
        batch_id = f"redownload_batch_{track_id}"

        # Fetch full track details from the metadata source for pipeline parity
        # This gives us track_number, disc_number, full album data
        meta_source = metadata.get('_source', '')
        meta_id = metadata.get('id', '')
        full_track_details = None
        full_album_data = None

        if meta_id:
            try:
                if meta_source == 'spotify' and spotify_client and spotify_client.is_authenticated():
                    full_track_details = spotify_client.get_track_details(meta_id)
                    if full_track_details and full_track_details.get('album', {}).get('id'):
                        full_album_data = spotify_client.get_album(full_track_details['album']['id'])
                elif meta_source == 'itunes':
                    _it = _get_itunes_client()
                    results = _it._lookup(id=meta_id, entity='song')
                    if results:
                        for r in results:
                            if r.get('wrapperType') == 'track':
                                full_track_details = r
                                break
                elif meta_source == 'deezer':
                    _dz = _get_deezer_client()
                    full_track_details = _dz._api_get(f'track/{meta_id}')
            except Exception as e:
                logger.debug(f"[Redownload] Could not fetch full track details: {e}")

        # Build track data with full metadata for pipeline parity
        track_number = None
        disc_number = 1
        album_data = {'name': metadata.get('album', '')}

        if full_track_details:
            if meta_source == 'spotify':
                track_number = full_track_details.get('track_number')
                disc_number = full_track_details.get('disc_number', 1)
                album_raw = full_track_details.get('album', {})
                if album_raw:
                    album_images = album_raw.get('images', [])
                    album_data = {
                        'id': album_raw.get('id', ''),
                        'name': album_raw.get('name', metadata.get('album', '')),
                        'release_date': album_raw.get('release_date', ''),
                        'album_type': album_raw.get('album_type', 'album'),
                        'total_tracks': album_raw.get('total_tracks', 0),
                        'images': album_images,
                        'image_url': album_images[0]['url'] if album_images else '',
                    }
            elif meta_source == 'itunes':
                track_number = full_track_details.get('trackNumber')
                disc_number = full_track_details.get('discNumber', 1)
            elif meta_source == 'deezer':
                track_number = full_track_details.get('track_position')
                disc_number = full_track_details.get('disk_number', 1)

        track_data = {
            'id': meta_id,
            'name': metadata.get('name', ''),
            'artists': [{'name': metadata.get('artist', '')}],
            'album': album_data,
            'duration_ms': metadata.get('duration_ms', 0),
            'track_number': track_number,
            'disc_number': disc_number,
            '_is_explicit_album_download': bool(full_album_data or (album_data.get('id'))),
        }

        # Build explicit context if we have full album data
        if full_album_data or album_data.get('id'):
            track_data['_explicit_album_context'] = full_album_data if isinstance(full_album_data, dict) else album_data
            track_data['_explicit_artist_context'] = {'name': metadata.get('artist', ''), 'id': '', 'genres': []}

        # Create batch
        with tasks_lock:
            download_batches[batch_id] = {
                'queue': [task_id],
                'queue_index': 1,  # Already past the first (only) item
                'active_count': 1,  # One worker is about to start
                'max_concurrent': 1,
                'playlist_id': f'redownload_{track_id}',
                'playlist_name': f"Redownload: {metadata.get('artist', '')} - {metadata.get('name', '')}",
                'phase': 'downloading',
                'total_tracks': 1,
                'completed_count': 0,
                'failed_count': 0,
                'cancelled_tracks': set(),
                'permanently_failed_tracks': [],
                'force_download': True,
                'auto_initiated': False,
            }

            download_tasks[task_id] = {
                'status': 'queued',
                'track_info': track_data,
                'playlist_id': f'redownload_{track_id}',
                'batch_id': batch_id,
                'track_index': 0,
                'download_id': None,
                'username': None,
                'filename': None,
                'retry_count': 0,
                'cached_candidates': [],
                'used_sources': set(),
                'status_change_time': time.time(),
                'metadata_enhanced': False,
                'error_message': None,
                '_redownload_context': {
                    'library_track_id': track_id,
                    'old_file_path': old_file_path,
                    'delete_old_file': delete_old,
                },
            }

        # Build a TrackResult-like candidate and submit to download
        def _run_redownload():
            try:
                from core.soulseek_client import TrackResult
                from core.itunes_client import Track as MetaTrack
                tr = TrackResult(
                    username=candidate['username'],
                    filename=candidate['filename'],
                    size=candidate.get('size', 0),
                    bitrate=candidate.get('bitrate', 0),
                    duration=candidate.get('duration', 0),
                    quality=candidate.get('quality', ''),
                    free_upload_slots=candidate.get('free_upload_slots', 0),
                    upload_speed=candidate.get('upload_speed', 0),
                    queue_length=candidate.get('queue_length', 0),
                )
                tr.artist = metadata.get('artist', '')
                tr.title = metadata.get('name', '')
                tr.album = metadata.get('album', '')
                tr.confidence = candidate.get('confidence', 1.0)

                # Build a proper Track object (not a dict) — _attempt_download_with_candidates
                # accesses track.artists, track.album etc. as attributes
                artist_name = metadata.get('artist', '')
                track_obj = MetaTrack(
                    id=metadata.get('id', ''),
                    name=metadata.get('name', ''),
                    artists=[artist_name] if artist_name else ['Unknown'],
                    album=metadata.get('album', ''),
                    duration_ms=metadata.get('duration_ms', 0),
                    popularity=0,
                )

                _attempt_download_with_candidates(task_id, [tr], track_obj, batch_id)
            except Exception as e:
                logger.error(f"Redownload failed: {e}", exc_info=True)
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = str(e)

        missing_download_executor.submit(_run_redownload)

        return jsonify({
            "success": True,
            "task_id": task_id,
            "batch_id": batch_id,
            "message": "Redownload started",
        })
    except Exception as e:
        logger.error(f"Error starting redownload: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
