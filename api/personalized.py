"""Personalized playlist endpoints - lifted from web_server.py.

bodies byte-identical; only the decorator changed.
"""

from flask import Blueprint, jsonify, request

from core.personalized import api as _personalized_api
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
_build_personalized_manager = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"personalized.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('personalized', __name__)


def create_blueprint():
    return bp

@bp.route('/api/personalized/kinds', methods=['GET'])
def personalized_list_kinds():
    """List every registered personalized-playlist kind. Includes the
    resolved variant list per kind that supports variants so the UI
    can render kind+variant checkboxes without per-kind round-trips."""
    try:
        manager = _build_personalized_manager()
        return jsonify(_personalized_api.list_kinds(manager=manager))
    except Exception as e:
        logger.error(f"Personalized kinds list error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/personalized/playlists', methods=['GET'])
def personalized_list_playlists():
    """List every persisted personalized playlist for the active profile."""
    try:
        manager = _build_personalized_manager()
        return jsonify(_personalized_api.list_playlists(manager, get_current_profile_id()))
    except Exception as e:
        logger.error(f"Personalized playlists list error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/personalized/playlist/<kind>', methods=['GET'])
@bp.route('/api/personalized/playlist/<kind>/<variant>', methods=['GET'])
def personalized_get_playlist(kind, variant=''):
    """Get one personalized playlist + its current track snapshot.

    Auto-creates the row from default config if it doesn't exist."""
    try:
        manager = _build_personalized_manager()
        return jsonify(_personalized_api.get_playlist_with_tracks(
            manager, kind, variant, get_current_profile_id(),
        ))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Personalized playlist get error ({kind}/{variant}): {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/personalized/playlist/<kind>/refresh', methods=['POST'])
@bp.route('/api/personalized/playlist/<kind>/<variant>/refresh', methods=['POST'])
def personalized_refresh_playlist(kind, variant=''):
    """Run the kind's generator and persist the snapshot."""
    try:
        manager = _build_personalized_manager()
        body = request.get_json(silent=True) or {}
        overrides = body.get('config_overrides') if isinstance(body.get('config_overrides'), dict) else None
        return jsonify(_personalized_api.refresh_playlist(
            manager, kind, variant, get_current_profile_id(), config_overrides=overrides,
        ))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Personalized playlist refresh error ({kind}/{variant}): {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/personalized/playlist/<kind>/config', methods=['PUT'])
@bp.route('/api/personalized/playlist/<kind>/<variant>/config', methods=['PUT'])
def personalized_update_config(kind, variant=''):
    """Patch the playlist's per-instance config."""
    try:
        manager = _build_personalized_manager()
        body = request.get_json(silent=True) or {}
        return jsonify(_personalized_api.update_config(
            manager, kind, variant, get_current_profile_id(), body,
        ))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Personalized playlist config error ({kind}/{variant}): {e}")
        return jsonify({"success": False, "error": str(e)}), 500
