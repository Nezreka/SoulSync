"""Wishlist endpoints - lifted from web_server.py.

process/count/stats/cycle/tracks/download_missing/clear/cleanup, the
remove family (track/album/artist/batch), the ignore list, and the
add-album-to-wishlist bridge. bodies byte-identical; only the decorator
changed. the auto-processing flag pair lives in core.runtime_state (moved
in the same change), so this module and the pipeline reset callback in
web_server share it by module attribute, not by injection.
"""

import json
import threading
import time

from flask import Blueprint, jsonify, request

from core import runtime_state as _rt_state
from core.profile_context import get_current_profile_id
from core.runtime_state import (
    add_activity_item,
    download_batches,
    download_tasks,
    tasks_lock,
)
from core.wishlist.processing import (
    WishlistManualDownloadRuntime as _WishlistManualDownloadRuntime,
    cleanup_wishlist_against_library as _cleanup_wishlist_against_library,
    start_manual_wishlist_download_batch as _start_manual_wishlist_download_batch,
)
from core.wishlist.routes import (
    WishlistRouteRuntime as _WishlistRouteRuntime,
    add_album_track_to_wishlist as _wishlist_add_album_track_to_wishlist,
    clear_wishlist as _wishlist_clear_wishlist,
    get_wishlist_count as _wishlist_get_wishlist_count,
    get_wishlist_cycle as _wishlist_get_wishlist_cycle,
    get_wishlist_stats as _wishlist_get_wishlist_stats,
    get_wishlist_tracks as _wishlist_get_wishlist_tracks,
    process_wishlist_api as _wishlist_process_api,
    remove_album_from_wishlist as _wishlist_remove_album_from_wishlist,
    remove_artist_from_wishlist as _wishlist_remove_artist_from_wishlist,
    remove_batch_from_wishlist as _wishlist_remove_batch_from_wishlist,
    remove_track_from_wishlist as _wishlist_remove_track_from_wishlist,
    set_wishlist_cycle as _wishlist_set_wishlist_cycle,
)
from core.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure() - stable boot objects and helpers, bound once
album_bundle_executor = None
automation_engine = None
get_database = None
missing_download_executor = None
wishlist_timer_lock = None
check_download_permission = None
is_wishlist_actually_processing = None
_get_batch_max_concurrent = None
_parse_requested_quality_profile_id = None
_process_wishlist_automatically = None
_run_full_missing_tracks_process = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"wishlist_routes.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('wishlist_routes', __name__)


def create_blueprint():
    return bp

@bp.route('/api/wishlist/process', methods=['POST'])
def process_wishlist_api():
    """Trigger wishlist processing via API. Processes pending wishlist tracks in the background."""
    try:
        # #1134: this passed is_auto_processing_flag=<raw wishlist_auto_processing
        # lambda> — a kwarg the factory never accepted, so the route 500'd on
        # every call. The factory's DEFAULT (is_wishlist_actually_processing)
        # is also the better guard: it verifies the worker is really alive,
        # while the raw flag goes stale after a crash — the same staleness
        # that kept the reporter's auto-timer "busy" over a dead batch.
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_process_api(
            runtime,
            start_processing=lambda: _process_wishlist_automatically(),
        )
        return jsonify(payload), status_code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/count', methods=['GET'])
def get_wishlist_count():
    """Endpoint to get current wishlist count."""
    try:
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_get_wishlist_count(runtime)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error getting wishlist count: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/wishlist/stats', methods=['GET'])
def get_wishlist_stats():
    """
    Get wishlist statistics broken down by category.

    Returns:
        {
            "singles": int,  # Count of singles + EPs
            "albums": int,   # Count of album tracks
            "total": int     # Total count
        }
    """
    try:
        runtime = _build_wishlist_route_runtime(
            get_next_run_seconds=(
                automation_engine.get_system_automation_next_run_seconds if automation_engine else None
            ),
        )
        payload, status_code = _wishlist_get_wishlist_stats(runtime)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error getting wishlist stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@bp.route('/api/wishlist/cycle', methods=['GET'])
def get_wishlist_cycle():
    """
    Get the current wishlist processing cycle.

    Returns:
        {"cycle": "albums" | "singles"}
    """
    try:
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_get_wishlist_cycle(runtime)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error getting wishlist cycle: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/wishlist/cycle', methods=['POST'])
def set_wishlist_cycle():
    """
    Set the current wishlist processing cycle.

    Body:
        {"cycle": "albums" | "singles"}
    """
    try:
        data = request.get_json()
        cycle = data.get('cycle')
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_set_wishlist_cycle(runtime, cycle)
        return jsonify(payload), status_code

    except Exception as e:
        logger.error(f"Error setting wishlist cycle: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery/lookback-period', methods=['GET'])
def get_discovery_lookback_period():
    """
    Get the discovery pool lookback period setting.

    Returns:
        {"period": "7" | "30" | "90" | "180" | "all"}
    """
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        # Get lookback period from metadata table
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'discovery_lookback_period'")
            row = cursor.fetchone()

            if row:
                period = row['value']
            else:
                # Default to 30 days on first access
                period = '30'
                cursor.execute("""
                    INSERT OR REPLACE INTO metadata (key, value, updated_at)
                    VALUES ('discovery_lookback_period', '30', CURRENT_TIMESTAMP)
                """)
                conn.commit()

        return jsonify({"period": period})

    except Exception as e:
        logger.error(f"Error getting discovery lookback period: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery/lookback-period', methods=['POST'])
def set_discovery_lookback_period():
    """
    Set the discovery pool lookback period setting.

    Body:
        {"period": "7" | "30" | "90" | "180" | "all"}
    """
    try:
        data = request.get_json()
        period = data.get('period')

        valid_periods = ['7', '30', '90', '180', 'all']
        if period not in valid_periods:
            return jsonify({"error": f"Invalid period. Must be one of: {', '.join(valid_periods)}"}), 400

        from database.music_database import MusicDatabase
        db = MusicDatabase()

        # Store lookback period in metadata table
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('discovery_lookback_period', ?, CURRENT_TIMESTAMP)
            """, (period,))

            # Set a one-time rescan cutoff so the next scan cycle uses the new
            # lookback window for artists that were already scanned under the old setting.
            # This avoids wiping last_scan_timestamp (which is needed for UI display).
            if period == 'all':
                # 'all' means no cutoff — store empty to signal "scan everything"
                rescan_value = ''
            else:
                from datetime import datetime, timedelta, timezone
                cutoff = datetime.now(timezone.utc) - timedelta(days=int(period))
                rescan_value = cutoff.isoformat()

            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('watchlist_rescan_cutoff', ?, CURRENT_TIMESTAMP)
            """, (rescan_value,))

            conn.commit()

        logger.info(f"Discovery lookback period set to: {period}")
        return jsonify({"success": True, "period": period})

    except Exception as e:
        logger.error(f"Error setting discovery lookback period: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery/hemisphere', methods=['GET'])
def get_hemisphere():
    """Get the hemisphere setting for seasonal content."""
    try:
        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'hemisphere'")
            row = cursor.fetchone()
            value = 'northern'
            if row:
                val = row[0] if isinstance(row, tuple) else row['value']
                if val in ('northern', 'southern'):
                    value = val
        return jsonify({"hemisphere": value})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/discovery/hemisphere', methods=['POST'])
def set_hemisphere():
    """Set the hemisphere for seasonal content (northern or southern)."""
    try:
        data = request.get_json()
        hemisphere = data.get('hemisphere', '').lower()
        if hemisphere not in ('northern', 'southern'):
            return jsonify({"error": "Must be 'northern' or 'southern'"}), 400

        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('hemisphere', ?, CURRENT_TIMESTAMP)
            """, (hemisphere,))
            conn.commit()

        logger.info("Hemisphere set to: %s", hemisphere)
        return jsonify({"success": True, "hemisphere": hemisphere})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/wishlist/tracks', methods=['GET'])
def get_wishlist_tracks():
    """
    Endpoint to get wishlist tracks for display in modal.
    Supports category filtering via query parameter.

    Query Parameters:
        category (optional): 'singles' or 'albums' - filters tracks by album type
        limit (optional): Maximum number of tracks to return (for performance)
    """
    try:
        category = request.args.get('category', None)  # None = all tracks
        limit = request.args.get('limit', type=int, default=None)  # None = no limit
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_get_wishlist_tracks(runtime, category=category, limit=limit)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error getting wishlist tracks: {e}")
        return jsonify({"error": str(e)}), 500

def _build_wishlist_route_runtime(
    *,
    is_actually_processing_fn=None,
    reset_wishlist_processing_state=None,
    get_next_run_seconds=None,
):
    from database.music_database import MusicDatabase

    return _WishlistRouteRuntime(
        get_music_database=MusicDatabase,
        profile_id=get_current_profile_id(),
        download_batches=download_batches,
        download_tasks=download_tasks,
        tasks_lock=tasks_lock,
        is_wishlist_actually_processing=is_actually_processing_fn or is_wishlist_actually_processing,
        reset_wishlist_processing_state=reset_wishlist_processing_state or (lambda: None),
        add_activity_item=add_activity_item,
        active_server=config_manager.get_active_media_server(),
        get_next_run_seconds=get_next_run_seconds,
    )

@bp.route('/api/wishlist/download_missing', methods=['POST'])
def start_wishlist_missing_downloads():
    """
    This endpoint fetches wishlist tracks and manages them with batch processing
    identical to playlist processing, maintaining exactly 3 concurrent downloads.
    """
    dl_err = check_download_permission()
    if dl_err:
        return dl_err
    try:
        # Check if auto-processing is currently running (prevent concurrent wishlist access)
        if is_wishlist_actually_processing():
            return jsonify({
                "error": "Wishlist auto-processing is currently running. Please wait for it to complete.",
                "retry_after": 30
            }), 409

        data = request.get_json() or {}
        from database.music_database import MusicDatabase

        db = MusicDatabase()
        manual_profile_id = get_current_profile_id()
        manual_runtime = _WishlistManualDownloadRuntime(
            get_music_database=lambda: db,
            download_batches=download_batches,
            tasks_lock=tasks_lock,
            missing_download_executor=missing_download_executor,
            album_bundle_executor=album_bundle_executor,
            run_full_missing_tracks_process=_run_full_missing_tracks_process,
            get_batch_max_concurrent=_get_batch_max_concurrent,
            add_activity_item=add_activity_item,
            active_server=config_manager.get_active_media_server(),
            profile_id=manual_profile_id,
        )

        payload, status_code = _start_manual_wishlist_download_batch(
            manual_runtime,
            track_ids=data.get('track_ids'),
            category=data.get('category'),
            force_download_all=data.get('force_download_all', False),
        )
        return jsonify(payload), status_code

    except Exception as e:
        logger.error(f"Error starting wishlist download process: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/clear', methods=['POST'])
def clear_wishlist():
    """Endpoint to clear all tracks from the wishlist.
    Also cancels any active wishlist download batch so cleared tracks don't keep downloading."""
    try:
        def _reset_wishlist_processing_state():
            with wishlist_timer_lock:
                _rt_state.wishlist_auto_processing = False
                _rt_state.wishlist_auto_processing_timestamp = 0

        runtime = _build_wishlist_route_runtime(
            reset_wishlist_processing_state=_reset_wishlist_processing_state,
        )
        payload, status_code = _wishlist_clear_wishlist(runtime)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error clearing wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/cleanup', methods=['POST'])
def cleanup_wishlist():
    """Endpoint to remove tracks from wishlist that already exist in the database."""
    try:
        from core.wishlist_service import get_wishlist_service
        from database.music_database import MusicDatabase

        wishlist_service = get_wishlist_service()
        db = MusicDatabase()
        active_server = config_manager.get_active_media_server()
        payload, status_code = _cleanup_wishlist_against_library(
            wishlist_service,
            db,
            get_current_profile_id(),
            active_server,
        )
        return jsonify(payload), status_code

    except Exception as e:
        logger.error(f"Error in wishlist cleanup: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/remove-track', methods=['POST'])
def remove_track_from_wishlist():
    """Endpoint to remove a single track from the wishlist."""
    try:
        data = request.get_json()
        spotify_track_id = data.get('spotify_track_id')
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_remove_track_from_wishlist(runtime, spotify_track_id)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error removing track from wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/remove-album', methods=['POST'])
def remove_album_from_wishlist():
    """Endpoint to remove all tracks from an album from the wishlist."""
    try:
        data = request.get_json()
        album_id = data.get('album_id')
        album_name_filter = data.get('album_name')
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_remove_album_from_wishlist(
            runtime,
            album_id=album_id,
            album_name_filter=album_name_filter,
        )
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error removing album from wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/remove-artist', methods=['POST'])
def remove_artist_from_wishlist():
    """Remove every wishlist track by one artist (#1065 — one click instead
    of unchecking a whole discography album by album)."""
    try:
        data = request.get_json() or {}
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_remove_artist_from_wishlist(
            runtime, artist_name=data.get('artist_name'))
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error removing artist from wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/remove-batch', methods=['POST'])
def remove_batch_from_wishlist():
    """Endpoint to remove multiple tracks from the wishlist."""
    try:
        data = request.get_json()
        spotify_track_ids = data.get('spotify_track_ids', [])
        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_remove_batch_from_wishlist(runtime, spotify_track_ids)
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error batch removing from wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/ignore-list', methods=['GET'])
def get_wishlist_ignore_list():
    """#874: active (non-expired) wishlist ignore entries for this profile."""
    try:
        from core.wishlist_service import get_wishlist_service
        runtime = _build_wishlist_route_runtime()
        entries = get_wishlist_service().database.get_wishlist_ignore(profile_id=runtime.profile_id)
        from core.wishlist.ignore import IGNORE_TTL_DAYS
        return jsonify({"success": True, "entries": entries, "ttl_days": IGNORE_TTL_DAYS})
    except Exception as e:
        logger.error(f"Error reading wishlist ignore-list: {e}")
        return jsonify({"success": False, "error": str(e), "entries": []}), 500

@bp.route('/api/wishlist/ignore-list/remove', methods=['POST'])
def remove_from_wishlist_ignore_list():
    """#874: un-ignore a track so it can be auto-acquired again."""
    try:
        data = request.get_json() or {}
        track_id = data.get('track_id') or data.get('spotify_track_id')
        if not track_id:
            return jsonify({"success": False, "error": "No track_id provided"}), 400
        from core.wishlist_service import get_wishlist_service
        runtime = _build_wishlist_route_runtime()
        ok = get_wishlist_service().database.remove_from_wishlist_ignore(
            track_id, profile_id=runtime.profile_id)
        return jsonify({"success": True, "removed": ok})
    except Exception as e:
        logger.error(f"Error removing from wishlist ignore-list: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/wishlist/ignore-list/clear', methods=['POST'])
def clear_wishlist_ignore_list():
    """#874: clear the entire wishlist ignore-list for this profile."""
    try:
        from core.wishlist_service import get_wishlist_service
        runtime = _build_wishlist_route_runtime()
        count = get_wishlist_service().database.clear_wishlist_ignore(profile_id=runtime.profile_id)
        return jsonify({"success": True, "cleared": count})
    except Exception as e:
        logger.error(f"Error clearing wishlist ignore-list: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/add-album-to-wishlist', methods=['POST'])
def add_album_track_to_wishlist():
    """Endpoint to add a single track from an album to the wishlist."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        track = data.get('track')
        artist = data.get('artist')
        album = data.get('album')
        source_type = data.get('source_type', 'album')
        source_context = data.get('source_context', {})

        # The Quality Profile the user picked in the shared acquisition dialog.
        # Before P1-01 this field did not exist and every manual Search /
        # Discover / Library add silently used the global default.
        quality_profile_id, error = _parse_requested_quality_profile_id(data)
        if error:
            return error

        runtime = _build_wishlist_route_runtime()
        payload, status_code = _wishlist_add_album_track_to_wishlist(
            runtime,
            track=track,
            artist=artist,
            album=album,
            source_type=source_type,
            source_context=source_context,
            quality_profile_id=quality_profile_id,
        )
        return jsonify(payload), status_code
    except Exception as e:
        logger.error(f"Error adding track to wishlist: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
