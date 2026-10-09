"""user playlist endpoints - the playlists people make inside soulsync.

create, list for the add-to-playlist picker, add / remove / reorder tracks.
everything else (rename, delete, identify, sync, auto-sync) goes through the
mirrored-playlist routes, because a user playlist is a mirrored playlist.

these are the owner's own lists, so every by-id route is scoped to the
active profile, admin included: an admin adding a song from search should
never land it in someone else's playlist.
"""

from flask import Blueprint, jsonify, request

from core.playlists import user_playlists as up
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"user_playlists.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('user_playlists', __name__)


def create_blueprint():
    return bp


def _own_user_playlist(database, playlist_id):
    """the playlist if it's a user playlist of the active profile, else None.
    a foreign or non-user playlist reads exactly like a missing one."""
    playlist = database.get_mirrored_playlist(playlist_id, profile_id=get_current_profile_id())
    return playlist if up.is_user_playlist(playlist) else None


@bp.route('/api/user-playlists', methods=['GET'])
def list_user_playlists():
    try:
        return jsonify({"playlists": up.list_playlists(get_database(), get_current_profile_id())})
    except Exception as e:
        logger.error(f"Error listing user playlists: {e}")
        return jsonify({"error": "Failed to load playlists"}), 500


@bp.route('/api/user-playlists', methods=['POST'])
def create_user_playlist():
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        playlist_id = up.create_playlist(database, get_current_profile_id(), data.get('name'))
        result = {"success": True, "id": playlist_id}
        # the picker's "new playlist" makes it and adds the song in one go
        if data.get('tracks'):
            result.update(up.add_tracks(
                database, _own_user_playlist(database, playlist_id), data['tracks'],
            ))
        return jsonify(result)
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating user playlist: {e}")
        return jsonify({"error": "Failed to create playlist"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/tracks', methods=['POST'])
def add_user_playlist_tracks(playlist_id):
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404
        tracks = data.get('tracks')
        if not isinstance(tracks, list):
            return jsonify({"error": "tracks must be a list"}), 400
        result = up.add_tracks(
            database, playlist, tracks, allow_duplicates=data.get('allow_duplicates') is True,
        )
        return jsonify({"success": True, **result})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding to user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to add to playlist"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/tracks/<int:position>', methods=['DELETE'])
def remove_user_playlist_track(playlist_id, position):
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404
        count = up.remove_track(database, playlist, position)
        return jsonify({"success": True, "track_count": count})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error removing from user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to remove track"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/order', methods=['PUT'])
def reorder_user_playlist(playlist_id):
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404
        order = data.get('order')
        if not isinstance(order, list):
            return jsonify({"error": "order must be a list"}), 400
        up.reorder_tracks(database, playlist, order)
        return jsonify({"success": True})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error reordering user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to reorder playlist"}), 500
