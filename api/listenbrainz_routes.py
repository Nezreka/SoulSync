"""ListenBrainz + Last.fm radio endpoints - lifted from web_server.py.

the discover shelves (created-for/user/collaborative), the playlist read
with its trackless-radio guard, refresh, the lastfm radio search/generate
family, series detection, and the LB playlist state/discovery/sync
routes. bodies byte-identical; only the decorator changed and
lastfm_worker became a getter (rebound on settings save).
"""

import threading
import time

from flask import Blueprint, jsonify, request

from api.source_playlists import (
    _build_fix_modal_spotify_data,
    _cancel_source_sync,
    _extract_artist_name,
    _get_source_discovery_status,
    _get_source_sync_status,
    _join_artist_names,
    _run_listenbrainz_discovery_worker,
    _run_sync_task,
    listenbrainz_discovery_executor,
    listenbrainz_playlist_states,
)
from core.discovery.endpoints import (
    convert_results_to_spotify_tracks,
    playlist_name_safe as _pl_name_safe,
)
from core.profile_context import get_current_profile_id
from core.runtime_state import add_activity_item
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
sync_executor = None
sync_lock = None
sync_states = None
active_sync_workers = None
get_database = None
config_manager = None
_get_lb_discover_playlists = None
_get_profile_lb_manager = None
_lastfm_worker = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"listenbrainz_routes.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('listenbrainz_routes', __name__)


def create_blueprint():
    return bp

@bp.route('/api/discover/listenbrainz/created-for', methods=['GET'])
def get_listenbrainz_created_for():
    """Get playlists created for the user by ListenBrainz (from cache)"""
    try:
        return _get_lb_discover_playlists('created_for')
    except Exception as e:
        logger.error(f"Error getting cached ListenBrainz created-for playlists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/listenbrainz/user-playlists', methods=['GET'])
def get_listenbrainz_user_playlists():
    """Get user's own ListenBrainz playlists (from cache)"""
    try:
        return _get_lb_discover_playlists('user')
    except Exception as e:
        logger.error(f"Error getting cached ListenBrainz user playlists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/listenbrainz/collaborative', methods=['GET'])
def get_listenbrainz_collaborative():
    """Get collaborative ListenBrainz playlists (from cache)"""
    try:
        return _get_lb_discover_playlists('collaborative')
    except Exception as e:
        logger.error(f"Error getting cached ListenBrainz collaborative playlists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/listenbrainz/playlist/<playlist_mbid>', methods=['GET'])
def get_listenbrainz_playlist_tracks(playlist_mbid):
    """Get tracks from a specific ListenBrainz playlist (from cache, with on-demand refresh)"""
    try:
        lb_manager, username, source = _get_profile_lb_manager()
        tracks = lb_manager.get_cached_tracks(playlist_mbid)

        if not tracks:
            # Cache miss or stale entry with no tracks — try fetching from LB API.
            # NEVER for a last.fm radio pseudo-mbid: it does not exist on the
            # ListenBrainz API, so the delete-then-refetch below would
            # permanently destroy the radio playlist on a mere READ.
            if str(playlist_mbid).startswith('lastfm_radio_'):
                return jsonify({
                    "success": False,
                    "error": "Radio playlist has no cached tracks - regenerate it"
                }), 404
            if lb_manager.client.is_authenticated():
                logger.debug(f"Cache miss for playlist {playlist_mbid}, fetching from ListenBrainz...")
                # Remove stale playlist row (if any) so _update_playlist doesn't
                # skip due to matching track_count with 0 actual tracks
                existing_type = lb_manager.get_playlist_type(playlist_mbid) or 'created_for'
                lb_manager.delete_cached_playlist(playlist_mbid)
                full_playlist = lb_manager.client.get_playlist_details(playlist_mbid)
                if full_playlist:
                    lb_manager._update_playlist(full_playlist, existing_type)
                    tracks = lb_manager.get_cached_tracks(playlist_mbid)

        if not tracks:
            return jsonify({
                "success": False,
                "error": "Playlist not found in cache"
            }), 404

        return jsonify({
            "success": True,
            "tracks": tracks,
            "track_count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error getting cached ListenBrainz playlist tracks: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# Manual refresh endpoint for ListenBrainz
@bp.route('/api/discover/listenbrainz/refresh', methods=['POST'])
def refresh_listenbrainz():
    """Manually refresh ListenBrainz playlists cache"""
    try:
        lb_manager, username, source = _get_profile_lb_manager()
        result = lb_manager.update_all_playlists()

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error refreshing ListenBrainz: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# ========================================
# LAST.FM TRACK RADIO
# ========================================

@bp.route('/api/lastfm/configured', methods=['GET'])
def lastfm_configured():
    """Return whether a Last.fm API key is configured (used to gate the Radio section)."""
    lf = _lastfm_worker().client if _lastfm_worker() else None
    return jsonify({"configured": bool(lf and lf.api_key)})


@bp.route('/api/lastfm/search/tracks', methods=['GET'])
def lastfm_search_tracks():
    """Search Last.fm for tracks matching a query string.

    Query params:
        q: search query (track name, artist name, or both)

    Returns:
        JSON list of {name, artist, mbid, listeners}
    """
    try:
        q = request.args.get('q', '').strip()
        if not q or len(q) < 2:
            return jsonify({"success": False, "error": "Query too short", "results": []}), 400

        lf = _lastfm_worker().client if _lastfm_worker() else None
        if not lf or not lf.api_key:
            return jsonify({"success": False, "error": "Last.fm not configured", "results": []}), 400

        # Use raw API call to get multiple results (search_track only returns best match)
        data = lf._make_request('track.search', {'track': q, 'limit': 8})
        if not data:
            return jsonify({"success": True, "results": []})

        raw = data.get('results', {}).get('trackmatches', {}).get('track', [])
        if not isinstance(raw, list):
            raw = [raw] if raw else []

        results = []
        for t in raw:
            # Last.fm image array: [{#text: url, size: small/medium/large/extralarge}]
            image_url = lf.get_best_image(t.get('image', []))
            # last.fm's generic grey-star placeholder loads fine, so onError
            # never fires and every row shows the same star - send '' and let
            # the ui draw its own art-empty glyph instead
            if image_url and '2a96cbd8b46e442fc41c2b86b821562f' in image_url:
                image_url = ''
            # listeners arrives as '' or missing on some matches; int('')
            # raised and one bad row 500'd the WHOLE search
            try:
                listeners = int(t.get('listeners') or 0)
            except (TypeError, ValueError):
                listeners = 0
            results.append({
                'name': t.get('name', ''),
                'artist': t.get('artist', ''),
                'mbid': t.get('mbid', ''),
                'listeners': listeners,
                'image_url': image_url or '',
            })
        return jsonify({"success": True, "results": results})

    except Exception as e:
        logger.error(f"Error searching Last.fm tracks: {e}")
        return jsonify({"success": False, "error": str(e), "results": []}), 500


@bp.route('/api/lastfm/radio/generate', methods=['POST'])
def lastfm_radio_generate():
    """Generate a Last.fm Radio playlist from a seed track.

    Body JSON:
        track_name:  seed track title
        artist_name: seed artist name

    Creates/updates a 'lastfm_radio' playlist in the DB and adds it to
    listenbrainz_playlist_states in 'fresh' phase, ready for discovery.

    Returns:
        {success, playlist_mbid, title, track_count}
    """
    try:
        data = request.get_json() or {}
        track_name = (data.get('track_name') or '').strip()
        artist_name = (data.get('artist_name') or '').strip()

        if not track_name or not artist_name:
            return jsonify({"success": False, "error": "track_name and artist_name are required"}), 400

        lf = _lastfm_worker().client if _lastfm_worker() else None
        if not lf or not lf.api_key:
            return jsonify({"success": False, "error": "Last.fm not configured"}), 400

        # Fetch similar tracks from Last.fm
        similar = lf.get_similar_tracks(artist_name, track_name, limit=25)
        if not similar:
            return jsonify({"success": False, "error": "No similar tracks found on Last.fm"}), 404

        # Persist to DB via manager
        lb_manager, _username, _source = _get_profile_lb_manager()
        playlist_mbid = lb_manager.save_lastfm_radio_playlist(track_name, artist_name, similar)
        title = f"Last.fm Radio: {track_name} by {artist_name}"

        # Build playlist dict that mirrors the LB playlist format expected by the discovery pipeline
        playlist_data = {
            'identifier': f"lastfm_radio/{playlist_mbid}",
            'name': title,
            'title': title,
            'creator': 'Last.fm',
            'tracks': [
                {
                    'track_name': t['name'],
                    'artist_name': t['artist'],
                    'album_name': '',
                    'duration_ms': 0,
                }
                for t in similar
            ],
        }

        # Upsert into in-memory state (fresh phase — not yet discovered)
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            listenbrainz_playlist_states[state_key] = {
                'playlist_mbid': playlist_mbid,
                'playlist': playlist_data,
                'phase': 'fresh',
                'status': 'fresh',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(similar),
                'discovery_results': [],
                'created_at': time.time(),
                'last_accessed': time.time(),
            }
        else:
            # Refresh existing state (new seed data) but preserve phase if already discovered
            state = listenbrainz_playlist_states[state_key]
            if state['phase'] not in ('discovering',):
                state['playlist'] = playlist_data
                state['spotify_total'] = len(similar)
                state['last_accessed'] = time.time()

        logger.info(f"Last.fm Radio generated: '{title}' ({len(similar)} tracks) → {playlist_mbid}")
        return jsonify({
            "success": True,
            "playlist_mbid": playlist_mbid,
            "title": title,
            "track_count": len(similar),
        })

    except Exception as e:
        logger.error(f"Error generating Last.fm radio: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/listenbrainz/lastfm-radio', methods=['GET'])
def get_listenbrainz_lastfm_radio():
    """Get cached Last.fm Radio playlists (from DB cache).

    Does NOT require ListenBrainz authentication — Last.fm Radio playlists are
    generated independently of the LB account.
    """
    try:
        lb_manager, username, source = _get_profile_lb_manager()
        playlists = lb_manager.get_cached_playlists('lastfm_radio')

        formatted = [
            {
                "playlist": {
                    "identifier": f"https://listenbrainz.org/playlist/{p['playlist_mbid']}",
                    "title": p['title'],
                    "creator": p['creator'],
                    "track_count": p.get('track_count', 0),
                    "annotation": p.get('annotation', {}),
                    "track": [],
                }
            }
            for p in playlists
        ]
        return jsonify({"success": True, "playlists": formatted, "count": len(formatted), "username": username, "source": source})
    except Exception as e:
        logger.error(f"Error getting Last.fm radio playlists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================
# LISTENBRAINZ PLAYLIST MANAGEMENT (Discovery System)
# ========================================

@bp.route('/api/listenbrainz/series-detect', methods=['GET'])
def get_listenbrainz_series_detect():
    """Detect whether a LB playlist title belongs to a rotating series.

    Auto-mirror uses this to decide whether the resulting mirror
    row should point at a per-playlist MBID (one-off LB playlist)
    or a synthetic series id (e.g. ``lb_weekly_jams_<user>``) that
    rolls forward as ListenBrainz publishes new periods.

    Query: ``?title=<raw LB playlist title>``
    Response on a match:
        ``{matched: true, series_id, canonical_name,
           source: 'listenbrainz'|'lastfm'}``
    Response on no match:
        ``{matched: false}``
    """
    try:
        from core.playlists.lb_series import detect_series

        title = (request.args.get('title') or '').strip()
        match = detect_series(title)
        if match is None:
            return jsonify({"matched": False})
        return jsonify({
            "matched": True,
            "series_id": match.series_id,
            "canonical_name": match.canonical_name,
            "source": match.source_for_mirror,
        })
    except Exception as e:
        logger.error(f"Error detecting LB series: {e}")
        return jsonify({"matched": False, "error": str(e)}), 500


def _lb_state_key(playlist_mbid, profile_id=None):
    """Build profile-scoped key for listenbrainz_playlist_states"""
    if profile_id is None:
        profile_id = get_current_profile_id()
    return f"{profile_id}:{playlist_mbid}"

@bp.route('/api/listenbrainz/playlists', methods=['GET'])
def get_all_listenbrainz_playlists():
    """Get all stored ListenBrainz playlists for frontend hydration (scoped to current profile)"""
    try:
        playlists = []
        current_time = time.time()
        profile_id = get_current_profile_id()
        prefix = f"{profile_id}:"

        for state_key, state in listenbrainz_playlist_states.items():
            if not state_key.startswith(prefix):
                continue
            # Update access time when requested
            state['last_accessed'] = current_time
            playlist_mbid = state_key[len(prefix):]

            # Return essential data for card recreation
            playlist_info = {
                'playlist_mbid': playlist_mbid,
                'playlist': state['playlist'],
                'phase': state['phase'],
                'status': state['status'],
                'discovery_progress': state['discovery_progress'],
                'spotify_matches': state['spotify_matches'],
                'spotify_total': state['spotify_total'],
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
                'created_at': state['created_at'],
                'last_accessed': state['last_accessed']
            }
            playlists.append(playlist_info)

        logger.info(f"Returning {len(playlists)} stored ListenBrainz playlists for profile {profile_id}")
        return jsonify({"playlists": playlists})

    except Exception as e:
        logger.error(f"Error getting ListenBrainz playlists: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/state/<playlist_mbid>', methods=['GET'])
def get_listenbrainz_playlist_state(playlist_mbid):
    """Get specific ListenBrainz playlist state (detailed version)"""
    try:
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            return jsonify({"error": "ListenBrainz playlist not found"}), 404

        state = listenbrainz_playlist_states[state_key]
        state['last_accessed'] = time.time()

        # Return full state information (including results for modal hydration)
        response = {
            'playlist_mbid': playlist_mbid,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'created_at': state['created_at'],
            'last_accessed': state['last_accessed']
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting ListenBrainz playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/reset/<playlist_mbid>', methods=['POST'])
def reset_listenbrainz_playlist(playlist_mbid):
    """Reset ListenBrainz playlist to fresh phase (clear discovery/sync data)"""
    try:
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            return jsonify({"error": "ListenBrainz playlist not found"}), 404

        state = listenbrainz_playlist_states[state_key]

        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()

        # Reset state to fresh (preserve original playlist data)
        state['phase'] = 'fresh'
        state['status'] = 'cached'
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['sync_playlist_id'] = None
        state['converted_spotify_playlist_id'] = None
        state['sync_progress'] = {}
        state['discovery_future'] = None
        state['last_accessed'] = time.time()

        logger.info(f"Reset ListenBrainz playlist to fresh: {state['playlist']['title']}")
        return jsonify({"success": True, "phase": "fresh"})

    except Exception as e:
        logger.error(f"Error resetting ListenBrainz playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/remove/<playlist_mbid>', methods=['POST'])
def remove_listenbrainz_playlist(playlist_mbid):
    """Remove ListenBrainz playlist from state (doesn't affect cache)"""
    try:
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            return jsonify({"error": "ListenBrainz playlist not found"}), 404

        state = listenbrainz_playlist_states[state_key]

        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()

        # Remove from state
        del listenbrainz_playlist_states[state_key]

        logger.info(f"Removed ListenBrainz playlist from state: {playlist_mbid}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error removing ListenBrainz playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/discovery/start/<playlist_mbid>', methods=['POST'])
def start_listenbrainz_discovery(playlist_mbid):
    """Initialize and start Spotify discovery process for a ListenBrainz playlist"""
    try:
        data = request.get_json()
        playlist_data = data.get('playlist')

        if not playlist_data:
            return jsonify({"error": "Playlist data required"}), 400

        # Create or update state
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            # Initialize new state
            listenbrainz_playlist_states[state_key] = {
                'playlist_mbid': playlist_mbid,
                'playlist': playlist_data,
                'phase': 'discovering',
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(playlist_data.get('tracks', [])),
                'discovery_results': [],
                'created_at': time.time(),
                'last_accessed': time.time()
            }
            logger.info(f"Created new ListenBrainz playlist state: {playlist_data.get('name', 'Unknown')}")
        else:
            # State already exists, update it
            state = listenbrainz_playlist_states[state_key]
            if state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400

            # Reset for new discovery
            state['phase'] = 'discovering'
            state['status'] = 'discovering'
            state['discovery_progress'] = 0
            state['spotify_matches'] = 0
            state['discovery_results'] = []
            state['last_accessed'] = time.time()

        state = listenbrainz_playlist_states[state_key]

        # Add activity for discovery start
        playlist_name = playlist_data.get('name', 'Unknown Playlist')
        track_count = len(playlist_data.get('tracks', []))
        add_activity_item("", "ListenBrainz Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")

        # Start discovery worker (pass state_key for profile-scoped state access)
        future = listenbrainz_discovery_executor.submit(_run_listenbrainz_discovery_worker, state_key)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for ListenBrainz playlist: {playlist_name}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting ListenBrainz discovery: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/discovery/status/<playlist_mbid>', methods=['GET'])
def get_listenbrainz_discovery_status(playlist_mbid):
    """Get real-time discovery status for a ListenBrainz playlist"""
    return _get_source_discovery_status(listenbrainz_playlist_states, _lb_state_key(playlist_mbid), "ListenBrainz playlist not found", "ListenBrainz")

@bp.route('/api/listenbrainz/update-phase/<playlist_mbid>', methods=['POST'])
def update_listenbrainz_phase(playlist_mbid):
    """Update ListenBrainz playlist phase (for phase transitions and persistence)"""
    try:
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            return jsonify({"error": "ListenBrainz playlist not found"}), 404

        data = request.get_json() or {}
        new_phase = data.get('phase')

        if not new_phase:
            return jsonify({"error": "Phase is required"}), 400

        state = listenbrainz_playlist_states[state_key]
        state['phase'] = new_phase
        state['last_accessed'] = time.time()

        # Update download process ID if provided (for download persistence)
        if 'download_process_id' in data:
            state['download_process_id'] = data['download_process_id']
            logger.info(f"Updated ListenBrainz download_process_id: {data['download_process_id']}")

        # Update converted Spotify playlist ID if provided (for download persistence)
        if 'converted_spotify_playlist_id' in data:
            state['converted_spotify_playlist_id'] = data['converted_spotify_playlist_id']
            logger.info(f"Updated ListenBrainz converted_spotify_playlist_id: {data['converted_spotify_playlist_id']}")

        logger.info(f"Updated ListenBrainz playlist {playlist_mbid} phase to: {new_phase}")

        return jsonify({
            "success": True,
            "phase": new_phase
        })

    except Exception as e:
        logger.error(f"Error updating ListenBrainz playlist phase: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/discovery/update_match', methods=['POST'])
def update_listenbrainz_discovery_match():
    """Update a ListenBrainz discovery result with manually selected Spotify track"""
    try:
        data = request.get_json()
        identifier = data.get('identifier')  # playlist_mbid
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return jsonify({'error': 'Missing required fields'}), 400

        # Get the state (identifier is playlist_mbid)
        state = listenbrainz_playlist_states.get(_lb_state_key(identifier))

        if not state:
            return jsonify({'error': 'Discovery state not found'}), 404

        # Update the discovery result
        if track_index < len(state['discovery_results']):
            result = state['discovery_results'][track_index]

            # Was previously not found, now found
            if result['status_class'] == 'not-found' and spotify_track:
                state['spotify_matches'] += 1
            # Was previously found, now not found
            elif result['status_class'] == 'found' and not spotify_track:
                state['spotify_matches'] -= 1

            # Update result
            result['status'] = 'Found' if spotify_track else 'Not Found'
            result['status_class'] = 'found' if spotify_track else 'not-found'
            result['spotify_track'] = spotify_track.get('name', '') if spotify_track else ''
            # Join all artists (matching YouTube/Tidal/Beatport format)
            artists = spotify_track.get('artists', []) if spotify_track else []
            result['spotify_artist'] = _join_artist_names(artists) if isinstance(artists, list) else _extract_artist_name(artists)
            # Album comes as a string from the frontend fix modal
            album = spotify_track.get('album', '') if spotify_track else ''
            result['spotify_album'] = album if isinstance(album, str) else album.get('name', '') if isinstance(album, dict) else ''
            result['spotify_id'] = spotify_track.get('id', '') if spotify_track else ''

            if spotify_track:
                # Store spotify_data in the same format as other platforms.
                # Manual match from the fix modal — build a rich spotify_data
                # (album as dict with image info) matching the normal discovery
                # shape, and explicitly clear any prior wing-it flag since the
                # user picked a real metadata match.
                result['spotify_data'] = _build_fix_modal_spotify_data(spotify_track)
            else:
                result['spotify_data'] = None

            result['wing_it_fallback'] = False
            result['manual_match'] = True

            logger.info(f"Updated ListenBrainz match for track {track_index}: {result['status']}")
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Invalid track index'}), 400

    except Exception as e:
        logger.error(f"Error updating ListenBrainz discovery match: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def convert_listenbrainz_results_to_spotify_tracks(discovery_results):
    """Convert ListenBrainz discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "ListenBrainz")

@bp.route('/api/wing-it/sync', methods=['POST'])
def wing_it_sync():
    """Sync a playlist to the media server using raw track names — no metadata discovery."""
    try:
        data = request.get_json()
        tracks_raw = data.get('tracks', [])
        playlist_name = data.get('playlist_name', 'Wing It Playlist')

        if not tracks_raw:
            return jsonify({"error": "No tracks provided"}), 400

        # Convert raw tracks to dicts — _run_sync_task expects dicts with .get()
        sync_tracks = []
        for t in tracks_raw:
            artist_name = ''
            if isinstance(t.get('artists'), list) and t['artists']:
                a = t['artists'][0]
                artist_name = a.get('name', str(a)) if isinstance(a, dict) else str(a)
            elif t.get('artist_name'):
                artist_name = t['artist_name']

            album_name = ''
            if isinstance(t.get('album'), dict):
                album_name = t['album'].get('name', '')
            elif isinstance(t.get('album'), str):
                album_name = t['album']
            elif t.get('album_name'):
                album_name = t['album_name']

            sync_tracks.append({
                'id': t.get('id', f"wing_it_{len(sync_tracks)}"),
                'name': t.get('name', t.get('track_name', 'Unknown')),
                'artists': [{'name': artist_name}] if artist_name else [{'name': 'Unknown'}],
                'album': album_name,
                'duration_ms': t.get('duration_ms', 0),
            })

        if not sync_tracks:
            return jsonify({"error": "No valid tracks to sync"}), 400

        sync_playlist_id = f"wing_it_sync_{int(time.time())}"

        add_activity_item("", "Wing It Sync Started", f"'{playlist_name}' — {len(sync_tracks)} tracks", "Now")

        with sync_lock:
            sync_states[sync_playlist_id] = {
                "status": "starting",
                "playlist_name": playlist_name,
                "progress": {
                    "playlist_name": playlist_name,
                    "total_tracks": len(sync_tracks),
                    "progress": 0,
                }
            }

        # Pass wing_it flag via sync state so _run_sync_task can skip wishlist
        with sync_lock:
            sync_states[sync_playlist_id]['wing_it'] = True

        future = sync_executor.submit(_run_sync_task, sync_playlist_id, playlist_name, sync_tracks, None, get_current_profile_id())
        active_sync_workers[sync_playlist_id] = future

        logger.info(f"[Wing It] Started sync for: {playlist_name} ({len(sync_tracks)} tracks)")
        return jsonify({"success": True, "sync_playlist_id": sync_playlist_id})

    except Exception as e:
        logger.error(f"Error in Wing It sync: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/listenbrainz/sync/start/<playlist_mbid>', methods=['POST'])
def start_listenbrainz_sync(playlist_mbid):
    """Start sync process for a ListenBrainz playlist using discovered Spotify tracks"""
    try:
        state_key = _lb_state_key(playlist_mbid)
        if state_key not in listenbrainz_playlist_states:
            return jsonify({"error": "ListenBrainz playlist not found"}), 404

        state = listenbrainz_playlist_states[state_key]
        state['last_accessed'] = time.time()  # Update access time

        if state['phase'] not in ['discovered', 'sync_complete', 'download_complete']:
            return jsonify({"error": "ListenBrainz playlist not ready for sync"}), 400

        # Convert discovery results to Spotify tracks format
        spotify_tracks = convert_listenbrainz_results_to_spotify_tracks(state['discovery_results'])

        if not spotify_tracks:
            return jsonify({"error": "No Spotify matches found for sync"}), 400

        # Create a temporary playlist ID for sync tracking
        sync_playlist_id = f"listenbrainz_{playlist_mbid}"
        playlist_name = state['playlist']['name']

        # Add activity for sync start
        add_activity_item("", "ListenBrainz Sync Started", f"'{playlist_name}' - {len(spotify_tracks)} tracks", "Now")

        # Update ListenBrainz state
        state['phase'] = 'syncing'
        state['sync_playlist_id'] = sync_playlist_id
        state['sync_progress'] = {}

        # Start the sync using existing sync infrastructure
        sync_data = {
            'playlist_id': sync_playlist_id,
            'playlist_name': playlist_name,
            'tracks': spotify_tracks
        }

        with sync_lock:
            sync_states[sync_playlist_id] = {
                "status": "starting",
                "playlist_name": playlist_name,
                "progress": {
                    "playlist_name": playlist_name,
                    "total_tracks": len(spotify_tracks),
                    "progress": 0,
                }
            }

        # Submit sync task
        future = sync_executor.submit(_run_sync_task, sync_playlist_id, sync_data['playlist_name'], spotify_tracks, None, get_current_profile_id())
        active_sync_workers[sync_playlist_id] = future

        logger.info(f"Started ListenBrainz sync for: {playlist_name} ({len(spotify_tracks)} tracks)")
        return jsonify({"success": True, "sync_playlist_id": sync_playlist_id})

    except Exception as e:
        logger.error(f"Error starting ListenBrainz sync: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/listenbrainz/sync/status/<playlist_mbid>', methods=['GET'])
def get_listenbrainz_sync_status(playlist_mbid):
    """Get sync status for a ListenBrainz playlist"""
    return _get_source_sync_status(listenbrainz_playlist_states, _lb_state_key(playlist_mbid), "ListenBrainz playlist not found", "ListenBrainz", "ListenBrainz playlist", _pl_name_safe)

@bp.route('/api/listenbrainz/sync/cancel/<playlist_mbid>', methods=['POST'])
def cancel_listenbrainz_sync(playlist_mbid):
    """Cancel sync for a ListenBrainz playlist"""
    return _cancel_source_sync(listenbrainz_playlist_states, _lb_state_key(playlist_mbid), "ListenBrainz", "ListenBrainz playlist not found")
