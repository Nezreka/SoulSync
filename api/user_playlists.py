"""user playlist endpoints - the playlists people make inside soulsync.

create, list for the add-to-playlist picker, add / remove / reorder tracks,
plus the parts only an owned playlist has: cover, description, duplicate.
everything else (rename, delete, identify, sync, auto-sync) goes through the
mirrored-playlist routes, because a user playlist is a mirrored playlist.

these are the owner's own lists, so every by-id route is scoped to the
active profile, admin included: an admin adding a song from search should
never land it in someone else's playlist.
"""

import os

from flask import Blueprint, jsonify, request, send_file

from core.playlists import auto_identify
from core.playlists import user_playlists as up
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None
config_manager = None


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


def _cover_base_dir():
    base = getattr(config_manager, "base_dir", None) if config_manager else None
    return str(base) if base else None


def _identify_soon(playlist_id):
    """songs went in: identify them in the background so the playlist is
    always ready to sync. best effort, never fails the request."""
    try:
        auto_identify.request(int(playlist_id))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"could not queue identify for user playlist {playlist_id}: {e}")


def _not_found():
    return jsonify({"error": "Playlist not found"}), 404


@bp.route('/api/user-playlists', methods=['GET'])
def list_user_playlists():
    try:
        database = get_database()
        rows = up.list_playlists(database, get_current_profile_id())
        # playlists made before they identified themselves, or whose last pass
        # got cut short, catch up here. counting is two indexed queries each,
        # and a playlist that is all identified costs nothing more
        for row in rows:
            try:
                discovered, total = database.get_mirrored_playlist_discovery_counts(row['id'])
                if total and discovered < total:
                    _identify_soon(row['id'])
            except Exception as e:  # noqa: BLE001
                logger.debug(f"could not count user playlist {row['id']}: {e}")
        return jsonify({"playlists": rows})
    except Exception as e:
        logger.error(f"Error listing user playlists: {e}")
        return jsonify({"error": "Failed to load playlists"}), 500


@bp.route('/api/user-playlists', methods=['POST'])
def create_user_playlist():
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        is_req = bool(data.get('is_request_list'))
        playlist_id = up.create_playlist(
            database, get_current_profile_id(), data.get('name'), is_request_list=is_req
        )
        result = {"success": True, "id": playlist_id}
        # the picker's "new playlist" makes it and adds the song in one go
        if data.get('tracks'):
            result.update(up.add_tracks(
                database, _own_user_playlist(database, playlist_id), data['tracks'],
            ))
            _identify_soon(playlist_id)
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
            return _not_found()
        tracks = data.get('tracks')
        if not isinstance(tracks, list):
            return jsonify({"error": "tracks must be a list"}), 400
        result = up.add_tracks(
            database, playlist, tracks, allow_duplicates=data.get('allow_duplicates') is True,
        )
        if result.get('added'):
            _identify_soon(playlist_id)
        return jsonify({"success": True, **result})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding to user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to add to playlist"}), 500


def _tracks_arg(data):
    tracks = data.get('tracks')
    return tracks if isinstance(tracks, list) else None


@bp.route('/api/user-playlists/<int:playlist_id>/tracks/remove', methods=['POST'])
def remove_matching_user_playlist_tracks(playlist_id):
    """the picker's untick: drop these songs wherever they are in the list."""
    data = request.get_json(silent=True) or {}
    tracks = _tracks_arg(data)
    if tracks is None:
        return jsonify({"error": "tracks must be a list"}), 400
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        return jsonify({"success": True, **up.remove_matching(database, playlist, tracks)})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error removing songs from user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to remove from playlist"}), 500


@bp.route('/api/user-playlists/membership', methods=['POST'])
def user_playlist_membership():
    """which of your playlists already have these songs, for the picker's ticks."""
    data = request.get_json(silent=True) or {}
    tracks = _tracks_arg(data)
    if tracks is None:
        return jsonify({"error": "tracks must be a list"}), 400
    try:
        result = up.membership(get_database(), get_current_profile_id(), tracks)
        # json object keys are strings either way; say so rather than let it happen
        return jsonify({
            "success": True,
            "total": result['total'],
            "playlists": {str(k): v for k, v in result['playlists'].items()},
        })
    except Exception as e:
        logger.error(f"Error checking user playlist membership: {e}")
        return jsonify({"error": "Failed to check your playlists"}), 500


@bp.route('/api/user-playlists/liked', methods=['POST'])
def like_songs():
    data = request.get_json(silent=True) or {}
    tracks = _tracks_arg(data)
    if tracks is None:
        return jsonify({"error": "tracks must be a list"}), 400
    try:
        result = up.like(get_database(), get_current_profile_id(), tracks)
        if result.get('added'):
            _identify_soon(result['id'])
        return jsonify({"success": True, **result})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error liking songs: {e}")
        return jsonify({"error": "Failed to like"}), 500


@bp.route('/api/user-playlists/liked/remove', methods=['POST'])
def unlike_songs():
    data = request.get_json(silent=True) or {}
    tracks = _tracks_arg(data)
    if tracks is None:
        return jsonify({"error": "tracks must be a list"}), 400
    try:
        return jsonify({"success": True, **up.unlike(get_database(), get_current_profile_id(), tracks)})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error unliking songs: {e}")
        return jsonify({"error": "Failed to unlike"}), 500


@bp.route('/api/user-playlists/from-mirror/<int:mirror_id>', methods=['POST'])
def copy_mirror_to_user_playlist(mirror_id):
    """make a mirrored playlist yours: an editable copy in My Playlists. the
    mirror itself is untouched and keeps syncing from its source."""
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        mirror = database.get_mirrored_playlist(mirror_id, profile_id=profile_id)
        if not mirror or up.is_user_playlist(mirror):
            return _not_found()
        result = up.copy_mirror(database, mirror, profile_id, data.get('name'))
        # the mirror's matches came across; this only finishes what it hadn't
        _identify_soon(result['id'])
        return jsonify({"success": True, **result})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error copying mirror {mirror_id} to a user playlist: {e}")
        return jsonify({"error": "Failed to copy the playlist"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/tracks/<int:position>', methods=['DELETE'])
def remove_user_playlist_track(playlist_id, position):
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
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
            return _not_found()
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


@bp.route('/api/user-playlists/<int:playlist_id>', methods=['PATCH'])
def update_user_playlist(playlist_id):
    """description for now; rename stays on the mirrored custom-name route."""
    data = request.get_json(silent=True) or {}
    if 'description' not in data:
        return jsonify({"error": "Nothing to update"}), 400
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        description = up.set_description(database, playlist, data.get('description'))
        return jsonify({"success": True, "description": description})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to update playlist"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/duplicate', methods=['POST'])
def duplicate_user_playlist(playlist_id):
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        result = up.duplicate_playlist(
            database, playlist, data.get('name'), base_dir=_cover_base_dir(),
        )
        _identify_soon(result['id'])
        return jsonify({"success": True, **result})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error duplicating user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to duplicate playlist"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/cover', methods=['POST'])
def upload_user_playlist_cover(playlist_id):
    """multipart 'file': any image pillow reads, re-encoded to a square webp."""
    f = request.files.get('file')
    if not f:
        return jsonify({"error": "No image"}), 400
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        image_url = up.set_cover_upload(
            database, playlist, f, base_dir=_cover_base_dir(),
        )
        return jsonify({"success": True, "image_url": image_url})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error uploading cover for user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to set cover"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/cover', methods=['PUT'])
def set_user_playlist_cover(playlist_id):
    """JSON: {"image_url": "<http(s) url>"} or {"from_track": <1-based position>}."""
    data = request.get_json(silent=True) or {}
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        if 'image_url' in data:
            image_url = up.set_cover_url(database, playlist, data.get('image_url'))
        elif 'from_track' in data:
            image_url = up.set_cover_from_track(database, playlist, data.get('from_track'))
        else:
            return jsonify({"error": "Give an image_url or a from_track position"}), 400
        return jsonify({"success": True, "image_url": image_url})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error setting cover for user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to set cover"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/cover', methods=['DELETE'])
def clear_user_playlist_cover(playlist_id):
    try:
        database = get_database()
        playlist = _own_user_playlist(database, playlist_id)
        if not playlist:
            return _not_found()
        up.clear_cover(database, playlist, base_dir=_cover_base_dir())
        return jsonify({"success": True})
    except up.UserPlaylistError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error clearing cover for user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to clear cover"}), 500


@bp.route('/api/user-playlists/<int:playlist_id>/cover', methods=['GET'])
def get_user_playlist_cover(playlist_id):
    # Reads are NOT owner-scoped: the media-server sync fetches this URL with
    # no session (and Plex fetches it server-side), so get_current_profile_id()
    # would fall back to the admin and 404 every non-admin playlist's cover.
    # Same posture as the avatar endpoint: the file serves, everything else
    # about the playlist stays behind the profile gate.
    try:
        database = get_database()
        playlist = database.get_mirrored_playlist(playlist_id)
        if not up.is_user_playlist(playlist):
            return _not_found()
        path = up.cover_path(playlist_id, _cover_base_dir())
        if not os.path.isfile(path):
            return jsonify({"error": "No cover"}), 404
        resp = send_file(path, mimetype="image/webp", max_age=3600)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
    except Exception as e:
        logger.error(f"Error serving cover for user playlist {playlist_id}: {e}")
        return jsonify({"error": "Failed to load cover"}), 500
