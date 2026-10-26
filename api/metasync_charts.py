"""MetaSync chart endpoints for the web UI: list the offered charts, and open
one as the active profile's own playlist (core/metasync_charts.py).

Opening never syncs or downloads: it only gives the profile a playlist that
is already matched, which the existing Sync / Download buttons act on.
The node's side (the handoff) is POST /api/v1/metasync/charts in api/metasync.py.
"""

from flask import Blueprint, jsonify

from core import metasync_charts as charts
from core.playlists import auto_identify
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"metasync_charts.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('metasync_charts', __name__)


def create_blueprint():
    return bp


def identify_soon(playlist_id):
    """queue a background identify; best effort, never fails a request."""
    try:
        auto_identify.request(int(playlist_id))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"could not queue identify for chart playlist {playlist_id}: {e}")


@bp.route('/api/metasync/charts', methods=['GET'])
def list_metasync_charts():
    try:
        return jsonify({"charts": charts.list_charts(get_database(), get_current_profile_id())})
    except Exception as e:
        logger.error(f"Error listing MetaSync charts: {e}")
        return jsonify({"error": "Failed to load charts"}), 500


@bp.route('/api/metasync/charts/<key>/open', methods=['POST'])
def open_metasync_chart(key):
    profile_id = get_current_profile_id()
    if profile_id is None:
        return jsonify({"error": "Pick a profile first"}), 400
    try:
        playlist_id = charts.open_chart(get_database(), key, profile_id, identify_soon)
    except Exception as e:
        logger.error(f"Error opening MetaSync chart {key}: {e}")
        return jsonify({"error": "Could not open that chart"}), 500
    if not playlist_id:
        return jsonify({"error": "That chart is no longer offered"}), 404
    return jsonify({"playlist_id": playlist_id})
