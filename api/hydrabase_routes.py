"""Hydrabase P2P endpoints - lifted from web_server.py.

connect/disconnect/status/comparisons/send, the background comparison
runner, the is-active gate, and the websocket + comparison state they
guard (module-local now). bodies byte-identical; only the decorator
changed and hydrabase_client / dev_mode_enabled / spotify_client became
getters.
"""

import collections as _collections
import json
import threading
import time

from flask import Blueprint, jsonify, request

from api.source_playlists import (
    _get_metadata_fallback_client,
    _get_metadata_fallback_source,
)
from core.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
_hydrabase_client = None
_dev_mode_enabled = None
_set_dev_mode = None
_spotify_client = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"hydrabase_routes.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('hydrabase_routes', __name__)


def create_blueprint():
    return bp

_hydrabase_ws = None
_hydrabase_lock = threading.Lock()

_hydrabase_comparisons = _collections.OrderedDict()
_COMPARISON_MAX_ENTRIES = 50
_comparison_lock = threading.Lock()

def _is_hydrabase_active():
    """Check if Hydrabase is connected and enabled for metadata use."""
    try:
        from core.metadata.registry import is_hydrabase_enabled
        return is_hydrabase_enabled()
    except Exception:
        return False

def _run_background_comparison(query, hydrabase_counts=None):
    """Run Spotify + fallback source searches in background and store for comparison.

    Args:
        query: Search query string.
        hydrabase_counts: Optional pre-computed dict {'tracks': N, 'artists': N, 'albums': N}
                          from the primary search to avoid redundant Hydrabase round-trips.
    """
    def _worker():
        try:
            result = {'timestamp': time.time(), 'query': query}

            # Use pre-computed counts if available, otherwise fetch from Hydrabase
            if hydrabase_counts is not None:
                hydra_data = hydrabase_counts
            else:
                hydra_data = {'tracks': 0, 'artists': 0, 'albums': 0}
                if _is_hydrabase_active():
                    raw_t = _hydrabase_client().search_raw(query, 'track')
                    raw_ar = _hydrabase_client().search_raw(query, 'artists')
                    raw_al = _hydrabase_client().search_raw(query, 'album')
                    hydra_data = {
                        'tracks': len(raw_t) if raw_t else 0,
                        'artists': len(raw_ar) if raw_ar else 0,
                        'albums': len(raw_al) if raw_al else 0
                    }
            result['hydrabase'] = hydra_data

            # Spotify results
            spotify_data = {'tracks': 0, 'artists': 0, 'albums': 0}
            if _spotify_client() and _spotify_client().is_authenticated():
                try:
                    s_tracks = _spotify_client().search_tracks(query, limit=10)
                    s_artists = _spotify_client().search_artists(query, limit=10)
                    s_albums = _spotify_client().search_albums(query, limit=10)
                    spotify_data = {
                        'tracks': len(s_tracks),
                        'artists': len(s_artists),
                        'albums': len(s_albums)
                    }
                except Exception as e:
                    logger.debug(f"Comparison Spotify search failed: {e}")
            result['spotify'] = spotify_data

            # Fallback metadata source results (iTunes or Deezer)
            fallback_source = _get_metadata_fallback_source()
            fallback_data = {'tracks': 0, 'artists': 0, 'albums': 0}
            try:
                fallback_client = _get_metadata_fallback_client()
                f_tracks = fallback_client.search_tracks(query, limit=10)
                f_artists = fallback_client.search_artists(query, limit=10)
                f_albums = fallback_client.search_albums(query, limit=10)
                fallback_data = {
                    'tracks': len(f_tracks),
                    'artists': len(f_artists),
                    'albums': len(f_albums)
                }
            except Exception as e:
                logger.debug(f"Comparison {fallback_source} search failed: {e}")
            result['fallback'] = fallback_data
            result['fallback_source'] = fallback_source

            with _comparison_lock:
                _hydrabase_comparisons[query] = result
                while len(_hydrabase_comparisons) > _COMPARISON_MAX_ENTRIES:
                    _hydrabase_comparisons.popitem(last=False)

            logger.info(f"Background comparison stored for '{query}': H={hydra_data}, S={spotify_data}, {fallback_source.capitalize()}={fallback_data}")

        except Exception as e:
            logger.error(f"Background comparison failed for '{query}': {e}")

    threading.Thread(target=_worker, daemon=True).start()

@bp.route('/api/hydrabase/connect', methods=['POST'])
def hydrabase_connect():
    """Connect to a Hydrabase instance via WebSocket."""
    global _hydrabase_ws
    data = request.get_json()
    url = data.get('url', '').strip()
    api_key = data.get('api_key', '').strip()
    if not url or not api_key:
        return jsonify({"success": False, "error": "URL and API key required"}), 400
    try:
        import websocket
        with _hydrabase_lock:
            # Close existing connection if any
            if _hydrabase_ws:
                try:
                    _hydrabase_ws.close()
                except Exception as _e:
                    logger.debug("hydrabase connect-existing close: %s", _e)
            ws = websocket.create_connection(
                url,
                header={"x-api-key": api_key},
                timeout=10
            )
            _hydrabase_ws = ws
        # Save credentials for auto-reconnect
        config_manager.set('hydrabase.url', url)
        config_manager.set('hydrabase.api_key', api_key)
        config_manager.set('hydrabase.auto_connect', True)
        logger.info(f"[Hydrabase] Connected to {url}")
        return jsonify({"success": True, "message": "Connected"})
    except Exception as e:
        logger.error(f"[Hydrabase] Connection failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/hydrabase/disconnect', methods=['POST'])
def hydrabase_disconnect():
    """Disconnect from Hydrabase and disable dev mode."""
    global _hydrabase_ws
    with _hydrabase_lock:
        if _hydrabase_ws:
            try:
                _hydrabase_ws.close()
            except Exception as e:
                logger.debug("hydrabase disconnect close: %s", e)
            _hydrabase_ws = None
    config_manager.set('hydrabase.auto_connect', False)
    # Only disable dev mode if not using Hydrabase as a regular fallback source
    if _get_metadata_fallback_source() != 'hydrabase':
        _set_dev_mode(False)
    logger.info("[Hydrabase] Disconnected")
    return jsonify({"success": True})

@bp.route('/api/hydrabase/status')
def hydrabase_status():
    """Check if connected to Hydrabase."""
    try:
        connected = _hydrabase_ws is not None and _hydrabase_ws.connected
    except Exception:
        connected = False
    try:
        hydra_config = config_manager.get_hydrabase_config()
    except AttributeError:
        hydra_config = {}
    peer_count = None
    try:
        if _hydrabase_client() and _hydrabase_client().last_peer_count is not None:
            peer_count = _hydrabase_client().last_peer_count
    except NameError:
        pass
    return jsonify({
        "connected": connected,
        "saved_url": hydra_config.get('url', ''),
        "saved_api_key": hydra_config.get('api_key', ''),
        "auto_connect": hydra_config.get('auto_connect', False),
        "peer_count": peer_count
    })

@bp.route('/api/hydrabase/comparisons')
def hydrabase_comparisons():
    """Get recent comparison results (Hydrabase vs Spotify vs fallback source)."""
    if not _dev_mode_enabled():
        return jsonify({"success": False, "error": "Dev mode not active"}), 403
    with _comparison_lock:
        items = list(reversed(_hydrabase_comparisons.values()))
    return jsonify({"success": True, "comparisons": items})

@bp.route('/api/hydrabase/send', methods=['POST'])
def hydrabase_send():
    """Send a raw JSON payload to Hydrabase and return the response."""
    global _hydrabase_ws
    if not _dev_mode_enabled():
        return jsonify({"success": False, "error": "Dev mode not active"}), 403
    if not _hydrabase_ws or not _hydrabase_ws.connected:
        return jsonify({"success": False, "error": "Not connected to Hydrabase"}), 400
    data = request.get_json()
    payload = data.get('payload')
    if not payload:
        return jsonify({"success": False, "error": "No payload provided"}), 400
    try:
        message = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        with _hydrabase_lock:
            _hydrabase_ws.send(message)
            response = _hydrabase_ws.recv()
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            result = response
        logger.info("[Hydrabase] Sent payload — got response")
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"[Hydrabase] Send failed: {e}")
        with _hydrabase_lock:
            try:
                _hydrabase_ws.close()
            except Exception as _e:
                logger.debug("hydrabase send close: %s", _e)
            _hydrabase_ws = None
        return jsonify({"success": False, "error": str(e)}), 500
