"""Beatport chart discovery/sync endpoints - lifted from web_server.py.

the /api/beatport discovery + sync + charts routes that ride the
source_playlists spine (beatport_chart_states / executor / shared status
helpers). the chart browse/scrape surface itself lives in api/beatport.py
already - this is the playlist-ification half. bodies byte-identical;
only the decorator changed and spotify_client / matching_engine became
getters.
"""

import time

from flask import Blueprint, jsonify, request

from api.beatport import clean_beatport_text
from api.source_playlists import (
    _build_discovery_wing_it_stub,
    _build_fix_modal_spotify_data,
    _cancel_source_sync,
    _extract_artist_name,
    _get_metadata_fallback_client,
    _join_artist_names,
    _pause_enrichment_workers,
    _resume_enrichment_workers,
    _get_discovery_cache_key,
    _get_source_discovery_status,
    _get_source_sync_status,
    _run_sync_task,
    _save_source_bubble_snapshot,
    _submit_sync_task,
    _sync_discovery_results_to_mirrored,
    _update_source_discovery_match,
    _update_source_playlist_phase,
    _validate_discovery_cache_artist,
    beatport_chart_states,
    beatport_discovery_executor,
)
from core.discovery.scoring import _discovery_score_candidates
from core.metadata.cache import get_metadata_cache
from core.profile_context import get_current_profile_id
from core.runtime_state import add_activity_item
from core.spotify_client import _is_globally_rate_limited as _spotify_rate_limited
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
sync_executor = None
sync_lock = None
sync_states = None
get_database = None
config_manager = None
_get_active_discovery_source = None
_spotify_client = None
_matching_engine = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"beatport_charts.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('beatport_charts', __name__)


def create_blueprint():
    return bp

@bp.route('/api/beatport/discovery/start/<url_hash>', methods=['POST'])
def start_beatport_discovery(url_hash):
    """Start Spotify discovery for Beatport chart tracks"""
    import json
    try:
        logger.info(f"Starting Beatport discovery for: {url_hash}")

        # Get chart data from request body
        data = request.get_json() or {}
        logger.debug(f"Raw request data: {data}")

        chart_data = data.get('chart_data')
        logger.debug(f"Chart data extracted: {chart_data is not None}")

        # Debug logging
        if chart_data:
            logger.debug(f"Chart data keys: {list(chart_data.keys()) if isinstance(chart_data, dict) else 'Not a dict'}")
            logger.debug(f"Chart name: {chart_data.get('name') if isinstance(chart_data, dict) else 'N/A'}")
            if isinstance(chart_data, dict) and 'tracks' in chart_data:
                logger.debug(f"Number of tracks: {len(chart_data['tracks'])}")
                if chart_data['tracks']:
                    logger.debug(f"First track: {chart_data['tracks'][0]}")
        else:
            logger.warning("No chart data received")

        if not chart_data or not chart_data.get('tracks'):
            return jsonify({"error": "Chart data with tracks is required"}), 400

        # Initialize Beatport chart state (similar to YouTube)
        if url_hash not in beatport_chart_states:
            beatport_chart_states[url_hash] = {
                'chart': chart_data,
                'phase': 'fresh',
                'discovery_results': [],
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(chart_data['tracks']),
                'status': 'fresh',
                'last_accessed': time.time()
            }

        state = beatport_chart_states[url_hash]
        state['last_accessed'] = time.time()

        if state['phase'] == 'discovering':
            return jsonify({"error": "Discovery already in progress"}), 400

        # Update phase to discovering
        state['phase'] = 'discovering'
        state['status'] = 'discovering'
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0

        # Add activity for discovery start
        chart_name = chart_data.get('name', 'Unknown Chart')
        track_count = len(chart_data['tracks'])
        add_activity_item("", "Beatport Discovery Started", f"'{chart_name}' - {track_count} tracks", "Now")

        # Start discovery worker (capture profile ID while we have Flask context)
        beatport_chart_states[url_hash]['_profile_id'] = get_current_profile_id()
        future = beatport_discovery_executor.submit(_run_beatport_discovery_worker, url_hash)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for Beatport chart: {chart_name}")
        return jsonify({"success": True, "message": "Discovery started", "status": "discovering"})

    except Exception as e:
        logger.error(f"Error starting Beatport discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/discovery/status/<url_hash>', methods=['GET'])
def get_beatport_discovery_status(url_hash):
    """Get real-time discovery status for a Beatport chart"""
    return _get_source_discovery_status(beatport_chart_states, url_hash, "Beatport chart not found", "Beatport")


@bp.route('/api/beatport/discovery/update_match', methods=['POST'])
def update_beatport_discovery_match():
    """Update a Beatport discovery result with manually selected Spotify track"""
    try:
        data = request.get_json()
        identifier = data.get('identifier')  # url_hash
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return jsonify({'error': 'Missing required fields'}), 400

        # Get the state
        state = beatport_chart_states.get(identifier)

        if not state:
            return jsonify({'error': 'Discovery state not found'}), 404

        if track_index >= len(state['discovery_results']):
            return jsonify({'error': 'Invalid track index'}), 400

        # Update the result
        result = state['discovery_results'][track_index]
        old_status = result.get('status')

        # Update with user-selected track
        result['status'] = 'Found'
        result['status_class'] = 'found'
        result['spotify_track'] = spotify_track['name']
        result['spotify_artist'] = _join_artist_names(spotify_track['artists']) if isinstance(spotify_track['artists'], list) else _extract_artist_name(spotify_track['artists'])
        result['spotify_album'] = spotify_track['album']
        result['spotify_id'] = spotify_track['id']

        # Format duration (Beatport doesn't show duration in table, but store it anyway)
        duration_ms = spotify_track.get('duration_ms', 0)
        if duration_ms:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) // 1000
            result['duration'] = f"{minutes}:{seconds:02d}"
        else:
            result['duration'] = '0:00'

        # IMPORTANT: Also set spotify_data for sync/download compatibility.
        # Manual match from the fix modal — build a rich spotify_data (album
        # as dict with image info) matching the normal discovery shape, and
        # explicitly clear any prior wing-it flag since the user picked a
        # real metadata match.
        result['spotify_data'] = _build_fix_modal_spotify_data(spotify_track)
        result['wing_it_fallback'] = False

        result['manual_match'] = True  # Flag for tracking

        # Update match count if status changed from not found/error
        if old_status != 'found' and old_status != 'Found':
            state['spotify_matches'] = state.get('spotify_matches', 0) + 1

        logger.info(f"Manual match updated: beatport - {identifier} - track {track_index}")
        logger.info(f"   → {result['spotify_artist']} - {result['spotify_track']}")

        return jsonify({'success': True, 'result': result})

    except Exception as e:
        logger.error(f"Error updating Beatport discovery match: {e}")
        return jsonify({'error': str(e)}), 500


# clean_beatport_text moved to api/beatport.py (imported above).
# Beatport discovery worker logic lives in core/discovery/beatport.py.
from core.discovery import beatport as _discovery_beatport
def _build_beatport_discovery_deps():
    """Build the BeatportDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_beatport.BeatportDiscoveryDeps(
        beatport_chart_states=beatport_chart_states,
        spotify_client=_spotify_client(),
        matching_engine=_matching_engine(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        clean_beatport_text=clean_beatport_text,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        spotify_rate_limited=_spotify_rate_limited,
        discovery_score_candidates=_discovery_score_candidates,
        get_metadata_cache=get_metadata_cache,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
        sync_discovery_results_to_mirrored=_sync_discovery_results_to_mirrored,
    )


def _run_beatport_discovery_worker(url_hash):
    return _discovery_beatport.run_beatport_discovery_worker(
        url_hash, _build_beatport_discovery_deps()
    )


@bp.route('/api/beatport/sync/start/<url_hash>', methods=['POST'])
def start_beatport_sync(url_hash):
    """Start sync process for a Beatport chart using discovered Spotify tracks"""
    try:
        logger.info(f"Beatport sync start requested for: {url_hash}")

        if url_hash not in beatport_chart_states:
            logger.warning(f"Beatport chart not found: {url_hash}")
            return jsonify({"error": "Beatport chart not found"}), 404

        state = beatport_chart_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time

        logger.info(f"Beatport chart state: phase={state.get('phase')}, has_discovery_results={len(state.get('discovery_results', []))}")

        if state['phase'] not in ['discovered', 'sync_complete', 'download_complete']:
            logger.info(f"Beatport chart not ready for sync: {state['phase']}")
            return jsonify({"error": "Beatport chart not ready for sync"}), 400

        # Convert discovery results to Spotify tracks format
        spotify_tracks = convert_beatport_results_to_spotify_tracks(state['discovery_results'])

        if not spotify_tracks:
            return jsonify({"error": "No Spotify matches found for sync"}), 400

        # Create a temporary playlist ID for sync tracking
        sync_playlist_id = f"beatport_sync_{url_hash}_{int(time.time())}"

        # Initialize sync state
        state['sync_playlist_id'] = sync_playlist_id
        state['phase'] = 'syncing'
        state['sync_progress'] = {'status': 'starting', 'progress': 0}

        # Create sync job using existing infrastructure
        sync_data = {
            'id': sync_playlist_id,
            'name': state['chart']['name'],
            'tracks': spotify_tracks,
            'source': 'beatport',
            'source_id': url_hash
        }

        # Add to sync states using existing sync system
        with sync_lock:
            sync_states[sync_playlist_id] = {
                "status": "starting",
                "playlist_name": sync_data['name'],
                "progress": {
                    "playlist_name": sync_data['name'],
                    "total_tracks": len(spotify_tracks),
                    "progress": 0,
                }
            }

        # Start sync in background using existing thread pool
        future = sync_executor.submit(_run_sync_task, sync_playlist_id, sync_data['name'], spotify_tracks, None, get_current_profile_id())
        state['sync_future'] = future

        logger.info(f"Started Beatport sync for chart: {state['chart']['name']}")
        return jsonify({"success": True, "sync_id": sync_playlist_id})

    except Exception as e:
        logger.error(f"Error starting Beatport sync: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/sync/status/<url_hash>', methods=['GET'])
def get_beatport_sync_status(url_hash):
    """Get sync status for a Beatport chart"""
    try:
        if url_hash not in beatport_chart_states:
            return jsonify({"error": "Beatport chart not found"}), 404

        state = beatport_chart_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')

        if not sync_playlist_id:
            return jsonify({"error": "No sync process found"}), 404

        # Get sync status from sync states
        sync_state = sync_states.get(sync_playlist_id, {})

        response = {
            'status': sync_state.get('status', 'unknown'),
            'progress': sync_state.get('progress', {}),
            'sync_id': sync_playlist_id,
            'complete': sync_state.get('status') == 'finished',
            'error': sync_state.get('error')
        }

        # Check if sync completed successfully
        if sync_state.get('status') == 'finished':
            state['phase'] = 'sync_complete'
            # Extract playlist ID from sync result
            result = sync_state.get('result', {})
            state['converted_spotify_playlist_id'] = result.get('spotify_playlist_id')
            chart_name = state.get('chart', {}).get('name', 'Unknown Chart')
            add_activity_item("", "Sync Complete", f"Beatport chart '{chart_name}' synced successfully", "Now")
        elif sync_state.get('status') == 'error':
            state['phase'] = 'discovered'  # Revert on error
            chart_name = state.get('chart', {}).get('name', 'Unknown Chart')
            add_activity_item("", "Sync Failed", f"Beatport chart '{chart_name}' sync failed", "Now")

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting Beatport sync status: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/sync/cancel/<url_hash>', methods=['POST'])
def cancel_beatport_sync(url_hash):
    """Cancel sync for a Beatport chart"""
    try:
        if url_hash not in beatport_chart_states:
            return jsonify({"error": "Beatport chart not found"}), 404

        state = beatport_chart_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')

        if sync_playlist_id and sync_playlist_id in sync_states:
            # Cancel the sync using existing sync infrastructure
            with sync_lock:
                sync_states[sync_playlist_id] = {"status": "cancelled"}

            # Cancel future if still running
            if 'sync_future' in state and state['sync_future']:
                state['sync_future'].cancel()

        # Revert Beatport state
        state['phase'] = 'discovered'
        state['sync_playlist_id'] = None
        state['sync_progress'] = {}

        logger.warning(f"Cancelled Beatport sync for: {url_hash}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error cancelling Beatport sync: {e}")
        return jsonify({"error": str(e)}), 500

# ===================================================================
# BEATPORT CHART PERSISTENCE API ENDPOINTS
# ===================================================================

@bp.route('/api/beatport/charts', methods=['GET'])
def get_beatport_charts():
    """Get all persistent Beatport chart states for frontend hydration"""
    try:
        charts = []
        current_time = time.time()

        # Clean up old charts (older than 24 hours)
        to_remove = []
        for chart_hash, state in beatport_chart_states.items():
            last_accessed = state.get('last_accessed', 0)
            if current_time - last_accessed > 86400:  # 24 hours
                to_remove.append(chart_hash)
            else:
                # Include in response
                chart_info = {
                    'hash': chart_hash,
                    'name': state['chart']['name'],
                    'track_count': len(state['chart']['tracks']),
                    'phase': state.get('phase', 'fresh'),
                    'discovery_progress': state.get('discovery_progress', 0),
                    'spotify_matches': state.get('spotify_matches', 0),
                    'spotify_total': state.get('spotify_total', 0),
                    'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                    'download_process_id': state.get('download_process_id'),
                    'last_accessed': last_accessed,
                    'chart_data': state['chart']  # Full chart data for restoration
                }
                charts.append(chart_info)

        # Remove old charts
        for chart_hash in to_remove:
            del beatport_chart_states[chart_hash]
            logger.info(f"Cleaned up old Beatport chart: {chart_hash}")

        logger.info(f"Returning {len(charts)} Beatport charts for hydration")
        return jsonify(charts)

    except Exception as e:
        logger.error(f"Error getting Beatport charts: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/charts/status/<chart_hash>', methods=['GET'])
def get_beatport_chart_status(chart_hash):
    """Get individual Beatport chart status with full state data"""
    try:
        if chart_hash not in beatport_chart_states:
            return jsonify({"error": "Beatport chart not found"}), 404

        state = beatport_chart_states[chart_hash]
        state['last_accessed'] = time.time()  # Update access time

        # Return full state including discovery results for modal restoration
        response = {
            'hash': chart_hash,
            'phase': state.get('phase', 'fresh'),
            'status': state.get('status', 'fresh'),
            'discovery_progress': state.get('discovery_progress', 0),
            'spotify_matches': state.get('spotify_matches', 0),
            'spotify_total': state.get('spotify_total', 0),
            'discovery_results': state.get('discovery_results', []),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_playlist_id': state.get('sync_playlist_id'),
            'sync_progress': state.get('sync_progress', {}),
            'chart_data': state['chart']  # Full chart data
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting Beatport chart status: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/charts/update-phase/<chart_hash>', methods=['POST'])
def update_beatport_chart_phase(chart_hash):
    """Update Beatport chart phase (for modal close operations and reset)"""
    try:
        if chart_hash not in beatport_chart_states:
            return jsonify({"error": "Beatport chart not found"}), 404

        data = request.get_json() or {}
        new_phase = data.get('phase')
        is_reset = data.get('reset', False)

        if not new_phase:
            return jsonify({"error": "Phase is required"}), 400

        state = beatport_chart_states[chart_hash]
        state['phase'] = new_phase
        state['last_accessed'] = time.time()

        # Handle reset operation - clear discovery data
        if is_reset and new_phase == 'fresh':
            state['discovery_results'] = []
            state['discovery_progress'] = 0
            state['spotify_matches'] = 0
            state['status'] = 'fresh'
            state['converted_spotify_playlist_id'] = None
            state['download_process_id'] = None
            state['sync_playlist_id'] = None
            state['sync_progress'] = {}
            logger.info(f"Reset Beatport chart {chart_hash} to fresh state")
        else:
            # Handle other phase updates (like download phase transitions)
            converted_playlist_id = data.get('converted_spotify_playlist_id')
            if converted_playlist_id:
                state['converted_spotify_playlist_id'] = converted_playlist_id

            download_process_id = data.get('download_process_id')
            if download_process_id:
                state['download_process_id'] = download_process_id

        logger.info(f"Updated Beatport chart {chart_hash} phase to: {new_phase}")
        return jsonify({"success": True, "phase": new_phase})

    except Exception as e:
        logger.error(f"Error updating Beatport chart phase: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/beatport/charts/delete/<chart_hash>', methods=['DELETE'])
def delete_beatport_chart(chart_hash):
    """Delete a Beatport chart from backend storage"""
    try:
        if chart_hash not in beatport_chart_states:
            return jsonify({"error": "Beatport chart not found"}), 404

        chart_name = beatport_chart_states[chart_hash]['chart']['name']
        del beatport_chart_states[chart_hash]

        logger.info(f"Deleted Beatport chart: {chart_name}")
        return jsonify({"success": True, "message": f"Deleted chart: {chart_name}"})

    except Exception as e:
        logger.error(f"Error deleting Beatport chart: {e}")
        return jsonify({"error": str(e)}), 500

def convert_beatport_results_to_spotify_tracks(discovery_results):
    """Convert Beatport discovery results to Spotify tracks format for sync"""
    spotify_tracks = []

    for result in discovery_results:
        # Support both data formats: spotify_data (manual fixes) and individual fields (automatic discovery)
        if result.get('spotify_data'):
            spotify_data = result['spotify_data']

            # Convert artists from objects to strings if needed
            artists = spotify_data['artists']
            if isinstance(artists, list) and len(artists) > 0:
                if isinstance(artists[0], dict) and 'name' in artists[0]:
                    # Convert from [{'name': 'Artist'}] to ['Artist']
                    artists = [artist['name'] for artist in artists]

            track = {
                'id': spotify_data['id'],
                'name': spotify_data['name'],
                'artists': artists,
                'album': spotify_data['album'],
                'source': 'beatport'
            }
            if spotify_data.get('track_number'):
                track['track_number'] = spotify_data['track_number']
            if spotify_data.get('disc_number'):
                track['disc_number'] = spotify_data['disc_number']
            spotify_tracks.append(track)
        elif result.get('spotify_track') and result.get('status_class') == 'found':
            # Build from individual fields (automatic discovery format)
            album_val = result.get('spotify_album', '')
            album_dict = album_val if isinstance(album_val, dict) else {
                'name': album_val or result.get('spotify_track', 'Unknown Album'),
                'album_type': 'single',
                'images': [],
                'release_date': '',
                'total_tracks': 1,
            }
            spotify_tracks.append({
                'id': result.get('spotify_id', 'unknown'),
                'name': result.get('spotify_track', 'Unknown Track'),
                'artists': [result.get('spotify_artist', 'Unknown Artist')] if result.get('spotify_artist') else ['Unknown Artist'],
                'album': album_dict,
                'source': 'beatport'
            })

    return spotify_tracks
