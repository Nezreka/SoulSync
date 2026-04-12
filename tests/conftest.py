"""Shared pytest fixtures for SoulSync WebSocket tests.

Creates a minimal Flask+SocketIO app that replicates the relevant
endpoints and event handlers without importing the full web_server.py
(which would try to initialize Spotify, Soulseek, Plex, etc.)."""

import copy
import pytest
import threading
import time
from flask import Flask, jsonify
from flask_socketio import SocketIO, join_room, leave_room


# ---------------------------------------------------------------------------
# Fake state that mirrors the real web_server.py module-level globals
# ---------------------------------------------------------------------------

_DEFAULT_STATUS_CACHE = {
    'spotify': {'connected': True, 'response_time': 12.5, 'source': 'spotify'},
    'media_server': {'connected': True, 'response_time': 8.1, 'type': 'plex'},
    'soulseek': {'connected': True, 'response_time': 5.3, 'source': 'soulseek'},
}

_DEFAULT_WATCHLIST_STATE = {
    'count': 7,
    'next_run_in_seconds': 3600,
}

# Phase 2: Dashboard state defaults
_DEFAULT_SYSTEM_STATS = {
    'active_downloads': 2,
    'finished_downloads': 15,
    'download_speed': '1.2 MB/s',
    'active_syncs': 1,
    'uptime': '2:30:00',
    'memory_usage': '45.2%',
}

_DEFAULT_DB_STATS = {
    'artists': 350,
    'albums': 1200,
    'tracks': 14500,
    'database_size_mb': 48.75,
    'server_source': 'plex',
    'last_full_refresh': '2026-03-01T12:00:00',
}

_DEFAULT_WISHLIST_COUNT = {
    'count': 5,
}

# Phase 3: Enrichment worker state defaults
_ENRICHMENT_COMMON = {
    'enabled': True, 'running': True, 'paused': False, 'idle': False,
    'current_item': {'name': 'Pink Floyd', 'type': 'artist'},
    'stats': {'matched': 10, 'not_found': 2, 'pending': 50, 'errors': 0},
    'progress': {
        'artists': {'matched': 10, 'total': 50, 'percent': 20},
        'albums': {'matched': 0, 'total': 100, 'percent': 0},
        'tracks': {'matched': 0, 'total': 500, 'percent': 0},
    }
}

_DEFAULT_ENRICHMENT_STATUS = {
    'musicbrainz': copy.deepcopy(_ENRICHMENT_COMMON),
    'audiodb': copy.deepcopy(_ENRICHMENT_COMMON),
    'deezer': copy.deepcopy(_ENRICHMENT_COMMON),
    'spotify-enrichment': {**copy.deepcopy(_ENRICHMENT_COMMON), 'authenticated': True},
    'itunes-enrichment': copy.deepcopy(_ENRICHMENT_COMMON),
    'hydrabase': {
        'enabled': True, 'running': True, 'paused': False,
        'queue_size': 12, 'stats': {'sent': 100, 'dropped': 2, 'errors': 0},
    },
    'repair': {
        'enabled': True, 'running': True, 'paused': False, 'idle': False,
        'current_item': {'name': 'song.mp3', 'type': 'track'},
        'stats': {'scanned': 50, 'repaired': 3, 'skipped': 10, 'errors': 0, 'pending': 150},
        'progress': {
            'tracks': {'checked': 50, 'total': 200, 'percent': 25, 'repaired': 3},
        }
    },
}

# Phase 4: Tool progress state defaults
_DEFAULT_STREAM_STATE = {
    "status": "loading", "progress": 45,
    "track_info": {"artist": "Pink Floyd", "title": "Comfortably Numb"},
    "error_message": None,
}

_DEFAULT_QUALITY_SCANNER_STATE = {
    "status": "running", "phase": "Scanning...", "progress": 35,
    "processed": 35, "total": 100, "quality_met": 30,
    "low_quality": 5, "matched": 2, "error_message": "", "results": [],
}

_DEFAULT_DUPLICATE_CLEANER_STATE = {
    "status": "running", "phase": "Scanning...", "progress": 50,
    "files_scanned": 500, "total_files": 1000, "duplicates_found": 10,
    "deleted": 5, "space_freed": 52428800, "error_message": "",
}

_DEFAULT_RETAG_STATE = {
    "status": "running", "phase": "Retagging...", "progress": 25,
    "current_track": "song.mp3", "total_tracks": 200, "processed": 50,
    "error_message": "",
}

_DEFAULT_DB_UPDATE_STATE = {
    "status": "running", "phase": "Updating...", "progress": 40,
    "current_item": "Pink Floyd", "processed": 40, "total": 100,
    "error_message": "", "removed_artists": 0, "removed_albums": 0, "removed_tracks": 0,
}

_DEFAULT_METADATA_STATE = {
    "status": "running", "current_artist": "Pink Floyd",
    "processed": 10, "total": 50, "percentage": 20.0,
    "successful": 9, "failed": 1, "started_at": None, "completed_at": None,
    "error": None, "refresh_interval_days": 30,
}

_DEFAULT_LOGS_ACTIVITIES = [
    {"icon": "\U0001f3b5", "title": "Download Complete", "subtitle": "Artist - Song", "time": "Now"},
]

# Phase 5: Sync/Discovery/Scan state defaults
_DEFAULT_SYNC_STATES = {
    'test-playlist-1': {
        'status': 'syncing',
        'progress': {
            'total_tracks': 11, 'matched_tracks': 5, 'failed_tracks': 1,
            'progress': 45, 'current_step': 'Matching...', 'current_track': 'Test Song',
        },
        'playlist_id': 'test-playlist-1', 'playlist_name': 'Test Playlist',
    },
    # Phase 6: Platform-specific sync IDs
    'tidal_test-tidal-1': {
        'status': 'syncing',
        'progress': {
            'total_tracks': 8, 'matched_tracks': 3, 'failed_tracks': 0,
            'progress': 37, 'current_step': 'Matching...', 'current_track': 'Tidal Song',
        },
        'playlist_id': 'tidal_test-tidal-1', 'playlist_name': 'Tidal Test Playlist',
    },
    'youtube_test-yt-hash': {
        'status': 'syncing',
        'progress': {
            'total_tracks': 10, 'matched_tracks': 4, 'failed_tracks': 1,
            'progress': 50, 'current_step': 'Matching...', 'current_track': 'YT Song',
        },
        'playlist_id': 'youtube_test-yt-hash', 'playlist_name': 'YouTube Test Playlist',
    },
    'beatport_sync_test-bp-hash_1234': {
        'status': 'syncing',
        'progress': {
            'total_tracks': 15, 'matched_tracks': 7, 'failed_tracks': 2,
            'progress': 60, 'current_step': 'Matching...', 'current_track': 'BP Song',
        },
        'playlist_id': 'beatport_sync_test-bp-hash_1234', 'playlist_name': 'Beatport Test Chart',
    },
    'listenbrainz_test-lb-mbid': {
        'status': 'syncing',
        'progress': {
            'total_tracks': 12, 'matched_tracks': 6, 'failed_tracks': 0,
            'progress': 50, 'current_step': 'Matching...', 'current_track': 'LB Song',
        },
        'playlist_id': 'listenbrainz_test-lb-mbid', 'playlist_name': 'ListenBrainz Test Playlist',
    },
}

_DEFAULT_DISCOVERY_STATES = {
    'tidal': {
        'test-tidal-1': {
            'phase': 'discovering', 'status': 'running',
            'discovery_progress': 50, 'spotify_matches': 5, 'spotify_total': 10,
            'discovery_results': [
                {'tidal_track': {'name': 'Song A', 'artists': ['Artist A']},
                 'status': 'found', 'status_class': 'found',
                 'spotify_data': {'name': 'Song A', 'artists': ['Artist A'], 'album': 'Album A'},
                 'spotify_id': 'sp1', 'manual_match': False},
            ],
        }
    },
    'youtube': {
        'test-yt-hash': {
            'phase': 'discovering', 'status': 'running',
            'discovery_progress': 30, 'spotify_matches': 3, 'spotify_total': 10,
            'discovery_results': [
                {'index': 0, 'yt_track': 'Song B', 'yt_artist': 'Artist B',
                 'status': 'Found', 'status_class': 'found',
                 'spotify_track': 'Song B', 'spotify_artist': 'Artist B',
                 'spotify_album': 'Album B'},
            ],
        }
    },
    'beatport': {},
    'listenbrainz': {},
}

_DEFAULT_WATCHLIST_SCAN_STATE = {
    'status': 'scanning',
    'current_artist_name': 'Pink Floyd', 'current_album': 'Dark Side',
    'current_track_name': 'Money',
    'current_artist_image_url': '', 'current_album_image_url': '',
    'current_phase': 'scanning', 'recent_wishlist_additions': [],
}

_DEFAULT_MEDIA_SCAN_STATE = {
    'is_scanning': True, 'status': 'scanning',
    'progress_message': 'Scanning library...',
}

_DEFAULT_WISHLIST_STATS = {
    'is_auto_processing': False,
    'next_run_in_seconds': 120,
}

_status_cache = copy.deepcopy(_DEFAULT_STATUS_CACHE)
watchlist_state = copy.deepcopy(_DEFAULT_WATCHLIST_STATE)
download_batches = {}   # batch_id -> {phase, tasks, ...}
tasks_lock = threading.Lock()

# Phase 2: Dashboard state
system_stats = copy.deepcopy(_DEFAULT_SYSTEM_STATS)
activity_feed = []
activity_feed_lock = threading.Lock()
db_stats = copy.deepcopy(_DEFAULT_DB_STATS)
wishlist_count = copy.deepcopy(_DEFAULT_WISHLIST_COUNT)

# Phase 3: Enrichment worker state
enrichment_status = copy.deepcopy(_DEFAULT_ENRICHMENT_STATUS)

# Phase 4: Tool progress state
stream_state = copy.deepcopy(_DEFAULT_STREAM_STATE)
quality_scanner_state = copy.deepcopy(_DEFAULT_QUALITY_SCANNER_STATE)
duplicate_cleaner_state = copy.deepcopy(_DEFAULT_DUPLICATE_CLEANER_STATE)
retag_state = copy.deepcopy(_DEFAULT_RETAG_STATE)
db_update_state = copy.deepcopy(_DEFAULT_DB_UPDATE_STATE)
metadata_update_state = copy.deepcopy(_DEFAULT_METADATA_STATE)
logs_activities = copy.deepcopy(_DEFAULT_LOGS_ACTIVITIES)

# Phase 5: Sync/Discovery/Scan state
sync_states = copy.deepcopy(_DEFAULT_SYNC_STATES)
sync_lock = threading.Lock()
discovery_states = copy.deepcopy(_DEFAULT_DISCOVERY_STATES)
watchlist_scan_state = copy.deepcopy(_DEFAULT_WATCHLIST_SCAN_STATE)
media_scan_state = copy.deepcopy(_DEFAULT_MEDIA_SCAN_STATE)
wishlist_stats_state = copy.deepcopy(_DEFAULT_WISHLIST_STATS)


# ---------------------------------------------------------------------------
# Helpers (same signatures as real web_server.py)
# ---------------------------------------------------------------------------

def _build_status_payload():
    return {
        'spotify': dict(_status_cache['spotify']),
        'media_server': dict(_status_cache['media_server']),
        'soulseek': dict(_status_cache['soulseek']),
        'active_media_server': _status_cache['media_server'].get('type', 'plex'),
    }


def _build_watchlist_count_payload():
    return {
        'success': True,
        'count': watchlist_state['count'],
        'next_run_in_seconds': watchlist_state['next_run_in_seconds'],
    }


def _build_batch_status_data(batch_id, batch):
    """Simplified version — real one is ~200 lines."""
    return {
        'phase': batch.get('phase', 'downloading'),
        'tasks': batch.get('tasks', []),
        'active_count': batch.get('active_count', 0),
        'max_concurrent': batch.get('max_concurrent', 3),
        'playlist_id': batch.get('playlist_id', ''),
        'playlist_name': batch.get('playlist_name', ''),
    }


# Phase 2 helpers

def _build_system_stats():
    return dict(system_stats)


def _build_activity_feed_payload():
    with activity_feed_lock:
        return {'activities': list(activity_feed[-10:][::-1])}


def _build_db_stats():
    return dict(db_stats)


def _build_wishlist_count_payload():
    return dict(wishlist_count)


# Phase 3 helpers

def _build_enrichment_status(worker_name):
    return copy.deepcopy(enrichment_status.get(worker_name, {}))

ENRICHMENT_WORKERS = [
    'musicbrainz', 'audiodb', 'deezer',
    'spotify-enrichment', 'itunes-enrichment',
    'hydrabase', 'repair',
]

ENRICHMENT_ENDPOINTS = {
    'musicbrainz': '/api/musicbrainz/status',
    'audiodb': '/api/audiodb/status',
    'deezer': '/api/deezer/status',
    'spotify-enrichment': '/api/spotify-enrichment/status',
    'itunes-enrichment': '/api/itunes-enrichment/status',
    'hydrabase': '/api/hydrabase-worker/status',
    'repair': '/api/repair/status',
}

# Phase 4 helpers

TOOL_NAMES = [
    'stream', 'quality-scanner', 'duplicate-cleaner',
    'retag', 'db-update', 'metadata', 'logs',
]

TOOL_ENDPOINTS = {
    'stream': '/api/stream/status',
    'quality-scanner': '/api/quality-scanner/status',
    'duplicate-cleaner': '/api/duplicate-cleaner/status',
    'retag': '/api/retag/status',
    'db-update': '/api/database/update/status',
    'metadata': '/api/metadata/status',
    'logs': '/api/logs',
}


def _build_stream_status():
    return {
        "status": stream_state["status"],
        "progress": stream_state["progress"],
        "track_info": stream_state["track_info"],
        "error_message": stream_state["error_message"],
    }


def _build_quality_scanner_status():
    return dict(quality_scanner_state)


def _build_duplicate_cleaner_status():
    state_copy = duplicate_cleaner_state.copy()
    state_copy["space_freed_mb"] = duplicate_cleaner_state["space_freed"] / (1024 * 1024)
    return state_copy


def _build_retag_status():
    return dict(retag_state)


def _build_db_update_status():
    return dict(db_update_state)


def _build_metadata_status():
    state_copy = metadata_update_state.copy()
    if state_copy.get('started_at'):
        state_copy['started_at'] = state_copy['started_at'].isoformat()
    if state_copy.get('completed_at'):
        state_copy['completed_at'] = state_copy['completed_at'].isoformat()
    return {"success": True, "status": state_copy}


def _build_logs():
    recent = logs_activities[-50:][::-1]
    formatted = []
    for a in recent:
        ts = a.get('time', 'Unknown')
        icon = a.get('icon', '\u2022')
        title = a.get('title', 'Activity')
        sub = a.get('subtitle', '')
        formatted.append(f"[{ts}] {icon} {title} - {sub}" if sub else f"[{ts}] {icon} {title}")
    if not formatted:
        formatted = ["No recent activity.", "Sync and download operations..."]
    return {'logs': formatted}


def _build_tool_status(tool_name):
    """Dispatcher that returns the correct status payload for any tool."""
    builders = {
        'stream': _build_stream_status,
        'quality-scanner': _build_quality_scanner_status,
        'duplicate-cleaner': _build_duplicate_cleaner_status,
        'retag': _build_retag_status,
        'db-update': _build_db_update_status,
        'metadata': _build_metadata_status,
        'logs': _build_logs,
    }
    return builders[tool_name]()


# Phase 5 helpers

SYNC_ENDPOINTS = {
    'sync': '/api/sync/status/test-playlist-1',
    # Phase 6: Platform-specific sync endpoints (use generic sync status)
    'tidal_sync': '/api/sync/status/tidal_test-tidal-1',
    'youtube_sync': '/api/sync/status/youtube_test-yt-hash',
    'beatport_sync': '/api/sync/status/beatport_sync_test-bp-hash_1234',
    'listenbrainz_sync': '/api/sync/status/listenbrainz_test-lb-mbid',
}

DISCOVERY_ENDPOINTS = {
    'tidal': '/api/tidal/discovery/status/test-tidal-1',
    'youtube': '/api/youtube/discovery/status/test-yt-hash',
}

SCAN_ENDPOINTS = {
    'watchlist': '/api/watchlist/scan/status',
    'media': '/api/scan/status',
    'wishlist_stats': '/api/wishlist/stats',
}


def _build_sync_status(playlist_id):
    with sync_lock:
        state = sync_states.get(playlist_id, {})
        return dict(state) if state else {'status': 'not_found'}


def _build_discovery_status(platform, pid):
    states = discovery_states.get(platform, {})
    state = states.get(pid, {})
    if not state:
        return {'error': 'Not found'}
    return {
        'phase': state.get('phase'),
        'status': state.get('status', 'unknown'),
        'progress': state.get('discovery_progress', 0),
        'spotify_matches': state.get('spotify_matches', 0),
        'spotify_total': state.get('spotify_total', 0),
        'results': state.get('discovery_results', []),
        'complete': state.get('phase') == 'discovered',
    }


def _build_watchlist_scan_status():
    return {"success": True, **watchlist_scan_state}


def _build_media_scan_status():
    return {"success": True, "status": dict(media_scan_state)}


def _build_wishlist_stats():
    return dict(wishlist_stats_state)


# Shared reference for socketio — set during test_app fixture
_test_socketio = None


def add_activity_item(icon, title, subtitle, time_ago="Now", show_toast=True):
    """Mirrors web_server.py's add_activity_item with instant toast push."""
    activity_item = {
        'icon': icon,
        'title': title,
        'subtitle': subtitle,
        'time': time_ago,
        'timestamp': time.time(),
        'show_toast': show_toast,
    }
    with activity_feed_lock:
        activity_feed.append(activity_item)
        if len(activity_feed) > 20:
            activity_feed.pop(0)

    # Instant toast push via WebSocket
    if show_toast and _test_socketio is not None:
        try:
            _test_socketio.emit('dashboard:toast', activity_item)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_app():
    """Create a minimal Flask + SocketIO app that mirrors Phase 1+2 endpoints."""
    global _test_socketio

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.start_time = time.time()
    socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')
    _test_socketio = socketio

    # --- Phase 1 HTTP endpoints ---

    @app.route('/status')
    def get_status():
        return jsonify(_build_status_payload())

    @app.route('/api/watchlist/count')
    def get_watchlist_count_endpoint():
        return jsonify(_build_watchlist_count_payload())

    @app.route('/api/download_status/batch')
    def get_batched_download_statuses():
        from flask import request
        requested_ids = request.args.getlist('batch_ids')
        response = {'batches': {}}
        with tasks_lock:
            target = {bid: b for bid, b in download_batches.items()
                      if not requested_ids or bid in requested_ids}
            for bid, batch in target.items():
                response['batches'][bid] = _build_batch_status_data(bid, batch)
        response['metadata'] = {
            'total_batches': len(response['batches']),
            'requested_batch_ids': requested_ids,
            'timestamp': time.time(),
        }
        return jsonify(response)

    # --- Phase 2 HTTP endpoints ---

    @app.route('/api/system/stats')
    def get_system_stats():
        try:
            return jsonify(_build_system_stats())
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/activity/feed')
    def get_activity_feed():
        try:
            return jsonify(_build_activity_feed_payload())
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/activity/toasts')
    def get_recent_toasts():
        try:
            current_time = time.time()
            with activity_feed_lock:
                recent_toasts = [
                    a for a in activity_feed
                    if a.get('show_toast', True) and
                       (current_time - a.get('timestamp', 0)) <= 10
                ]
            return jsonify({'toasts': recent_toasts})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/database/stats')
    def get_database_stats():
        try:
            return jsonify(_build_db_stats())
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/wishlist/count')
    def get_wishlist_count_api():
        try:
            return jsonify(_build_wishlist_count_payload())
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # --- Phase 3 HTTP endpoints (enrichment workers) ---

    @app.route('/api/musicbrainz/status')
    def musicbrainz_status():
        return jsonify(_build_enrichment_status('musicbrainz'))

    @app.route('/api/audiodb/status')
    def audiodb_status():
        return jsonify(_build_enrichment_status('audiodb'))

    @app.route('/api/deezer/status')
    def deezer_status():
        return jsonify(_build_enrichment_status('deezer'))

    @app.route('/api/spotify-enrichment/status')
    def spotify_enrichment_status():
        return jsonify(_build_enrichment_status('spotify-enrichment'))

    @app.route('/api/itunes-enrichment/status')
    def itunes_enrichment_status():
        return jsonify(_build_enrichment_status('itunes-enrichment'))

    @app.route('/api/hydrabase-worker/status')
    def hydrabase_worker_status():
        return jsonify(_build_enrichment_status('hydrabase'))

    @app.route('/api/repair/status')
    def repair_status():
        return jsonify(_build_enrichment_status('repair'))

    # --- Phase 4 HTTP endpoints (tool progress) ---

    @app.route('/api/stream/status')
    def stream_status_endpoint():
        return jsonify(_build_stream_status())

    @app.route('/api/quality-scanner/status')
    def quality_scanner_status_endpoint():
        return jsonify(_build_quality_scanner_status())

    @app.route('/api/duplicate-cleaner/status')
    def duplicate_cleaner_status_endpoint():
        return jsonify(_build_duplicate_cleaner_status())

    @app.route('/api/retag/status')
    def retag_status_endpoint():
        return jsonify(_build_retag_status())

    @app.route('/api/database/update/status')
    def db_update_status_endpoint():
        return jsonify(_build_db_update_status())

    @app.route('/api/metadata/status')
    def metadata_status_endpoint():
        return jsonify(_build_metadata_status())

    @app.route('/api/logs')
    def logs_endpoint():
        return jsonify(_build_logs())

    # --- Phase 5 HTTP endpoints (sync/discovery/scan) ---

    @app.route('/api/sync/status/<playlist_id>')
    def sync_status_endpoint(playlist_id):
        status = _build_sync_status(playlist_id)
        if status.get('status') == 'not_found':
            return jsonify({'error': 'Sync not found'}), 404
        return jsonify(status)

    @app.route('/api/tidal/discovery/status/<playlist_id>')
    def tidal_discovery_status_endpoint(playlist_id):
        return jsonify(_build_discovery_status('tidal', playlist_id))

    @app.route('/api/youtube/discovery/status/<url_hash>')
    def youtube_discovery_status_endpoint(url_hash):
        return jsonify(_build_discovery_status('youtube', url_hash))

    @app.route('/api/beatport/discovery/status/<url_hash>')
    def beatport_discovery_status_endpoint(url_hash):
        return jsonify(_build_discovery_status('beatport', url_hash))

    @app.route('/api/listenbrainz/discovery/status/<playlist_mbid>')
    def listenbrainz_discovery_status_endpoint(playlist_mbid):
        return jsonify(_build_discovery_status('listenbrainz', playlist_mbid))

    @app.route('/api/watchlist/scan/status')
    def watchlist_scan_status_endpoint():
        return jsonify(_build_watchlist_scan_status())

    @app.route('/api/scan/status')
    def media_scan_status_endpoint():
        return jsonify(_build_media_scan_status())

    @app.route('/api/wishlist/stats')
    def wishlist_stats_endpoint():
        return jsonify(_build_wishlist_stats())

    # --- Phase 1 WebSocket background emitters ---

    def _emit_service_status_loop():
        while True:
            socketio.sleep(10)
            try:
                socketio.emit('status:update', _build_status_payload())
            except Exception:
                pass

    def _emit_watchlist_count_loop():
        while True:
            socketio.sleep(30)
            try:
                socketio.emit('watchlist:count', _build_watchlist_count_payload())
            except Exception:
                pass

    def _emit_download_status_loop():
        while True:
            socketio.sleep(2)
            try:
                with tasks_lock:
                    for bid, batch in download_batches.items():
                        try:
                            socketio.emit('downloads:batch_update', {
                                'batch_id': bid,
                                'data': _build_batch_status_data(bid, batch),
                            }, room=f'batch:{bid}')
                        except Exception:
                            pass
            except Exception:
                pass

    # --- Phase 2 WebSocket background emitters ---

    def _emit_system_stats_loop():
        while True:
            socketio.sleep(10)
            try:
                socketio.emit('dashboard:stats', _build_system_stats())
            except Exception:
                pass

    def _emit_activity_feed_loop():
        while True:
            socketio.sleep(5)
            try:
                socketio.emit('dashboard:activity', _build_activity_feed_payload())
            except Exception:
                pass

    def _emit_db_stats_loop():
        while True:
            socketio.sleep(30)
            try:
                socketio.emit('dashboard:db_stats', _build_db_stats())
            except Exception:
                pass

    def _emit_wishlist_count_ws_loop():
        while True:
            socketio.sleep(30)
            try:
                socketio.emit('dashboard:wishlist_count', _build_wishlist_count_payload())
            except Exception:
                pass

    # Note: Toasts emit instantly from add_activity_item() — no timer needed

    # --- Phase 3 WebSocket background emitter ---

    def _emit_enrichment_status_loop():
        while True:
            socketio.sleep(2)
            for name in ENRICHMENT_WORKERS:
                try:
                    status = _build_enrichment_status(name)
                    if status:
                        socketio.emit(f'enrichment:{name}', status)
                except Exception:
                    pass

    # --- Phase 4 WebSocket background emitter ---

    def _emit_tool_progress_loop():
        while True:
            socketio.sleep(1)
            for name in TOOL_NAMES:
                try:
                    status = _build_tool_status(name)
                    if status:
                        socketio.emit(f'tool:{name}', status)
                except Exception:
                    pass

    # --- Phase 5 WebSocket background emitters ---

    def _emit_sync_progress_loop():
        while True:
            socketio.sleep(1)
            try:
                with sync_lock:
                    for pid, state in list(sync_states.items()):
                        try:
                            socketio.emit('sync:progress', {
                                'playlist_id': pid, **state
                            }, room=f'sync:{pid}')
                        except Exception:
                            pass
            except Exception:
                pass

    def _emit_discovery_progress_loop():
        while True:
            socketio.sleep(1)
            for platform in ['tidal', 'youtube', 'beatport', 'listenbrainz']:
                try:
                    states_dict = discovery_states.get(platform, {})
                    for pid, state in list(states_dict.items()):
                        try:
                            phase = state.get('phase', '')
                            if phase in ('', 'idle'):
                                continue
                            payload = {
                                'platform': platform,
                                'id': pid,
                                'phase': state.get('phase'),
                                'status': state.get('status', 'unknown'),
                                'progress': state.get('discovery_progress', 0),
                                'discovery_progress': state.get('discovery_progress', {}),
                                'spotify_matches': state.get('spotify_matches', 0),
                                'spotify_total': state.get('spotify_total', 0),
                                'results': state.get('discovery_results', state.get('results', [])),
                                'complete': state.get('phase') == 'discovered',
                            }
                            socketio.emit('discovery:progress', payload, room=f'discovery:{pid}')
                        except Exception:
                            pass
                except Exception:
                    pass

    def _emit_scan_status_loop():
        while True:
            socketio.sleep(2)
            try:
                socketio.emit('scan:watchlist', {"success": True, **watchlist_scan_state})
            except Exception:
                pass
            try:
                socketio.emit('scan:media', {"success": True, "status": dict(media_scan_state)})
            except Exception:
                pass
            try:
                socketio.emit('wishlist:stats', dict(wishlist_stats_state))
            except Exception:
                pass

    # --- Socket.IO event handlers ---

    @socketio.on('connect')
    def handle_connect():
        pass

    @socketio.on('disconnect')
    def handle_disconnect():
        pass

    @socketio.on('downloads:subscribe')
    def handle_download_subscribe(data):
        batch_ids = data.get('batch_ids', [])
        for bid in batch_ids:
            join_room(f'batch:{bid}')

    @socketio.on('downloads:unsubscribe')
    def handle_download_unsubscribe(data):
        batch_ids = data.get('batch_ids', [])
        for bid in batch_ids:
            leave_room(f'batch:{bid}')

    # Phase 5 subscribe/unsubscribe handlers
    @socketio.on('sync:subscribe')
    def handle_sync_subscribe(data):
        for pid in data.get('playlist_ids', []):
            join_room(f'sync:{pid}')

    @socketio.on('sync:unsubscribe')
    def handle_sync_unsubscribe(data):
        for pid in data.get('playlist_ids', []):
            leave_room(f'sync:{pid}')

    @socketio.on('discovery:subscribe')
    def handle_discovery_subscribe(data):
        for pid in data.get('ids', []):
            join_room(f'discovery:{pid}')

    @socketio.on('discovery:unsubscribe')
    def handle_discovery_unsubscribe(data):
        for pid in data.get('ids', []):
            leave_room(f'discovery:{pid}')

    # Start emitters (Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5)
    socketio.start_background_task(_emit_service_status_loop)
    socketio.start_background_task(_emit_watchlist_count_loop)
    socketio.start_background_task(_emit_download_status_loop)
    socketio.start_background_task(_emit_system_stats_loop)
    socketio.start_background_task(_emit_activity_feed_loop)
    socketio.start_background_task(_emit_db_stats_loop)
    socketio.start_background_task(_emit_wishlist_count_ws_loop)
    socketio.start_background_task(_emit_enrichment_status_loop)
    socketio.start_background_task(_emit_tool_progress_loop)
    socketio.start_background_task(_emit_sync_progress_loop)
    socketio.start_background_task(_emit_discovery_progress_loop)
    socketio.start_background_task(_emit_scan_status_loop)

    return app, socketio


@pytest.fixture
def flask_client(test_app):
    """Plain Flask test client (HTTP only)."""
    app, _socketio = test_app
    return app.test_client()


@pytest.fixture
def socketio_client(test_app):
    """Socket.IO test client (connects via WebSocket)."""
    app, socketio = test_app
    return socketio.test_client(app)


@pytest.fixture
def shared_state():
    """Provide direct references to the mutable state dicts AND helper functions.

    Using this fixture avoids import-path mismatches between pytest's
    auto-discovered conftest module and explicit ``from tests.conftest import …``."""
    return {
        # Phase 1 state
        'status_cache': _status_cache,
        'watchlist_state': watchlist_state,
        'download_batches': download_batches,
        'tasks_lock': tasks_lock,
        'build_status_payload': _build_status_payload,
        'build_watchlist_count_payload': _build_watchlist_count_payload,
        'build_batch_status_data': _build_batch_status_data,
        # Phase 2 state
        'system_stats': system_stats,
        'activity_feed': activity_feed,
        'activity_feed_lock': activity_feed_lock,
        'db_stats': db_stats,
        'wishlist_count': wishlist_count,
        'build_system_stats': _build_system_stats,
        'build_activity_feed_payload': _build_activity_feed_payload,
        'build_db_stats': _build_db_stats,
        'build_wishlist_count_payload_ws': _build_wishlist_count_payload,
        'add_activity_item': add_activity_item,
        # Phase 3 state
        'enrichment_status': enrichment_status,
        'build_enrichment_status': _build_enrichment_status,
        'enrichment_workers': ENRICHMENT_WORKERS,
        'enrichment_endpoints': ENRICHMENT_ENDPOINTS,
        # Phase 4 state
        'stream_state': stream_state,
        'quality_scanner_state': quality_scanner_state,
        'duplicate_cleaner_state': duplicate_cleaner_state,
        'retag_state': retag_state,
        'db_update_state': db_update_state,
        'metadata_update_state': metadata_update_state,
        'logs_activities': logs_activities,
        'build_tool_status': _build_tool_status,
        'build_stream_status': _build_stream_status,
        'build_quality_scanner_status': _build_quality_scanner_status,
        'build_duplicate_cleaner_status': _build_duplicate_cleaner_status,
        'build_retag_status': _build_retag_status,
        'build_db_update_status': _build_db_update_status,
        'build_metadata_status': _build_metadata_status,
        'build_logs': _build_logs,
        'tool_names': TOOL_NAMES,
        'tool_endpoints': TOOL_ENDPOINTS,
        # Phase 5 state
        'sync_states': sync_states,
        'sync_lock': sync_lock,
        'discovery_states': discovery_states,
        'watchlist_scan_state': watchlist_scan_state,
        'media_scan_state': media_scan_state,
        'build_sync_status': _build_sync_status,
        'build_discovery_status': _build_discovery_status,
        'build_watchlist_scan_status': _build_watchlist_scan_status,
        'build_media_scan_status': _build_media_scan_status,
        'wishlist_stats_state': wishlist_stats_state,
        'build_wishlist_stats': _build_wishlist_stats,
        'sync_endpoints': SYNC_ENDPOINTS,
        'discovery_endpoints': DISCOVERY_ENDPOINTS,
        'scan_endpoints': SCAN_ENDPOINTS,
    }


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all mutable state between tests."""
    # Reset to defaults
    _status_cache.clear()
    _status_cache.update(copy.deepcopy(_DEFAULT_STATUS_CACHE))
    watchlist_state.clear()
    watchlist_state.update(copy.deepcopy(_DEFAULT_WATCHLIST_STATE))
    download_batches.clear()
    # Phase 2 resets
    system_stats.clear()
    system_stats.update(copy.deepcopy(_DEFAULT_SYSTEM_STATS))
    with activity_feed_lock:
        activity_feed.clear()
    db_stats.clear()
    db_stats.update(copy.deepcopy(_DEFAULT_DB_STATS))
    wishlist_count.clear()
    wishlist_count.update(copy.deepcopy(_DEFAULT_WISHLIST_COUNT))
    # Phase 3 resets
    enrichment_status.clear()
    enrichment_status.update(copy.deepcopy(_DEFAULT_ENRICHMENT_STATUS))
    # Phase 4 resets
    stream_state.clear()
    stream_state.update(copy.deepcopy(_DEFAULT_STREAM_STATE))
    quality_scanner_state.clear()
    quality_scanner_state.update(copy.deepcopy(_DEFAULT_QUALITY_SCANNER_STATE))
    duplicate_cleaner_state.clear()
    duplicate_cleaner_state.update(copy.deepcopy(_DEFAULT_DUPLICATE_CLEANER_STATE))
    retag_state.clear()
    retag_state.update(copy.deepcopy(_DEFAULT_RETAG_STATE))
    db_update_state.clear()
    db_update_state.update(copy.deepcopy(_DEFAULT_DB_UPDATE_STATE))
    metadata_update_state.clear()
    metadata_update_state.update(copy.deepcopy(_DEFAULT_METADATA_STATE))
    logs_activities.clear()
    logs_activities.extend(copy.deepcopy(_DEFAULT_LOGS_ACTIVITIES))
    # Phase 5 resets
    sync_states.clear()
    sync_states.update(copy.deepcopy(_DEFAULT_SYNC_STATES))
    discovery_states.clear()
    discovery_states.update(copy.deepcopy(_DEFAULT_DISCOVERY_STATES))
    watchlist_scan_state.clear()
    watchlist_scan_state.update(copy.deepcopy(_DEFAULT_WATCHLIST_SCAN_STATE))
    media_scan_state.clear()
    media_scan_state.update(copy.deepcopy(_DEFAULT_MEDIA_SCAN_STATE))
    wishlist_stats_state.clear()
    wishlist_stats_state.update(copy.deepcopy(_DEFAULT_WISHLIST_STATS))
    yield
    # Cleanup after test
    _status_cache.clear()
    _status_cache.update(copy.deepcopy(_DEFAULT_STATUS_CACHE))
    watchlist_state.clear()
    watchlist_state.update(copy.deepcopy(_DEFAULT_WATCHLIST_STATE))
    download_batches.clear()
    system_stats.clear()
    system_stats.update(copy.deepcopy(_DEFAULT_SYSTEM_STATS))
    with activity_feed_lock:
        activity_feed.clear()
    db_stats.clear()
    db_stats.update(copy.deepcopy(_DEFAULT_DB_STATS))
    wishlist_count.clear()
    wishlist_count.update(copy.deepcopy(_DEFAULT_WISHLIST_COUNT))
    enrichment_status.clear()
    enrichment_status.update(copy.deepcopy(_DEFAULT_ENRICHMENT_STATUS))
    stream_state.clear()
    stream_state.update(copy.deepcopy(_DEFAULT_STREAM_STATE))
    quality_scanner_state.clear()
    quality_scanner_state.update(copy.deepcopy(_DEFAULT_QUALITY_SCANNER_STATE))
    duplicate_cleaner_state.clear()
    duplicate_cleaner_state.update(copy.deepcopy(_DEFAULT_DUPLICATE_CLEANER_STATE))
    retag_state.clear()
    retag_state.update(copy.deepcopy(_DEFAULT_RETAG_STATE))
    db_update_state.clear()
    db_update_state.update(copy.deepcopy(_DEFAULT_DB_UPDATE_STATE))
    metadata_update_state.clear()
    metadata_update_state.update(copy.deepcopy(_DEFAULT_METADATA_STATE))
    logs_activities.clear()
    logs_activities.extend(copy.deepcopy(_DEFAULT_LOGS_ACTIVITIES))
    # Phase 5 resets
    sync_states.clear()
    sync_states.update(copy.deepcopy(_DEFAULT_SYNC_STATES))
    discovery_states.clear()
    discovery_states.update(copy.deepcopy(_DEFAULT_DISCOVERY_STATES))
    watchlist_scan_state.clear()
    watchlist_scan_state.update(copy.deepcopy(_DEFAULT_WATCHLIST_SCAN_STATE))
    media_scan_state.clear()
    media_scan_state.update(copy.deepcopy(_DEFAULT_MEDIA_SCAN_STATE))
    wishlist_stats_state.clear()
    wishlist_stats_state.update(copy.deepcopy(_DEFAULT_WISHLIST_STATS))
