"""Music issues endpoints - lifted from web_server.py.

caller identity comes from the session (core.profile_context), the rules for
what may change from core.issues.lifecycle, shared with video.
"""

import json
import os

from flask import Blueprint, jsonify, request

from core.issues import service as issue_service
from core.issues.activity import fix_action
from core.issues.lifecycle import visible_to
from core.metadata import normalize_image_url as fix_artist_image_url
from core.profile_context import get_current_profile_id, is_admin_request
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None
_resolve_library_file_path = None
_get_audio_quality_string = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"issues.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('issues', __name__)


def create_blueprint():
    return bp


ENTITY_TYPES = ('artist', 'album', 'track')
CATEGORIES = ('wrong_track', 'wrong_metadata', 'wrong_cover', 'duplicate_tracks',
              'missing_tracks', 'audio_quality', 'wrong_artist', 'wrong_album',
              'incomplete_album', 'other')

def _caller():
    """(profile_id, is_admin) from the session. never a header: the old
    X-Profile-Id read let any profile claim to be the admin (or anyone), and
    no header at all meant profile 1."""
    return get_current_profile_id(), is_admin_request()


def _actor_name():
    try:
        from flask import g
        return getattr(g, 'profile_name', None) or ''
    except RuntimeError:
        return ''


def _notify(profile_id, message, kind='info'):
    from core.profile_notify import notify_profile
    notify_profile(profile_id, message, kind, link='issues')


def _side():
    db = get_database()

    def create(pid, entity_type, entity_id, category, title, description, snapshot, priority, _name):
        result = db.create_issue(profile_id=pid, entity_type=entity_type, entity_id=entity_id,
                                 category=category, title=title, description=description,
                                 snapshot_data=snapshot, priority=priority)
        return result.get('id') if result.get('success') else None

    return issue_service.IssueSide(
        get=db.get_issue, create=create,
        update=lambda iid, updates: bool(db.update_issue(iid, updates).get('success')),
        store=db.issue_threads, notify=_notify, categories=CATEGORIES)


def _fix_snapshot_thumbs(issue):
    snap = issue.get('snapshot_data')
    if isinstance(snap, dict):
        for key in ('thumb_url', 'artist_thumb', 'album_thumb'):
            if snap.get(key):
                snap[key] = fix_artist_image_url(snap[key]) or snap[key]


@bp.route('/api/issues', methods=['GET'])
def list_issues():
    """List issues. Admin sees all; non-admin sees own only."""
    try:
        database = get_database()
        profile_id, is_admin = _caller()
        if profile_id is None:
            return jsonify({"success": False, "error": "profile_required"}), 401

        status = request.args.get('status')
        category = request.args.get('category')
        entity_type = request.args.get('entity_type')
        try:
            limit = min(200, max(1, int(request.args.get('limit', 100))))
        except (ValueError, TypeError):
            limit = 100
        try:
            offset = max(0, int(request.args.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0

        result = database.get_issues(
            profile_id=profile_id,
            status=status,
            category=category,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
            is_admin=is_admin,
        )
        for issue in result.get('issues', []):
            _fix_snapshot_thumbs(issue)
            issue['fix_action'] = fix_action(issue.get('category') or '')
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues', methods=['POST'])
def create_issue():
    """Create a new library issue."""
    try:
        database = get_database()
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        profile_id, is_admin = _caller()
        if profile_id is None:
            return jsonify({"success": False, "error": "profile_required"}), 401
        entity_type = data.get('entity_type')
        entity_id = data.get('entity_id')
        category = data.get('category')

        if not entity_type or not entity_id or not category:
            return jsonify({"success": False, "error": "entity_type, entity_id, category, and title are required"}), 400

        if entity_type not in ENTITY_TYPES:
            return jsonify({"success": False, "error": f"entity_type must be one of: {', '.join(ENTITY_TYPES)}"}), 400
        if category not in CATEGORIES:
            return jsonify({"success": False, "error": f"Invalid category: {category}"}), 400

        payload, code = issue_service.report(
            _side(), actor=profile_id, actor_name=_actor_name(), is_admin=is_admin,
            entity_type=entity_type, entity_id=str(entity_id), category=category, body=data,
            snapshot=lambda: _build_issue_snapshot(database, entity_type, str(entity_id)))
        if code == 400:
            payload['error'] = "entity_type, entity_id, category, and title are required"
        return jsonify(payload), code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['GET'])
def get_issue(issue_id):
    """Get a single issue. someone else's reads as not found."""
    try:
        profile_id, is_admin = _caller()
        payload, code = issue_service.detail(_side(), actor=profile_id, is_admin=is_admin,
                                             issue_id=issue_id)
        if payload.get('issue'):
            _fix_snapshot_thumbs(payload['issue'])
        return jsonify(payload), code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['PUT'])
def update_issue(issue_id):
    """Update an issue (admin: respond/resolve; user: edit own title/description)."""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        profile_id, is_admin = _caller()
        payload, code = issue_service.triage(_side(), actor=profile_id, actor_name=_actor_name(),
                                             is_admin=is_admin, issue_id=issue_id, body=data)
        return jsonify(payload), code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['DELETE'])
def delete_issue(issue_id):
    """Delete an issue (admin or issue owner)."""
    try:
        database = get_database()
        profile_id, is_admin = _caller()
        issue = database.get_issue(issue_id)
        if not visible_to(issue, is_admin=is_admin, profile_id=profile_id):
            return jsonify({"success": False, "error": "Issue not found"}), 404

        result = database.delete_issue(issue_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/counts', methods=['GET'])
def get_issue_counts():
    """Get issue counts by status for badge display."""
    try:
        database = get_database()
        profile_id, is_admin = _caller()
        if profile_id is None:
            return jsonify({"success": False, "error": "profile_required"}), 401
        counts = database.get_issue_counts(is_admin=is_admin, profile_id=profile_id)
        # a member's badge: their reports with news they haven't opened
        counts['updates'] = 0 if is_admin else database.issue_threads.unread_count(profile_id)
        return jsonify({"success": True, "counts": counts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/bulk', methods=['POST'])
def bulk_issues():
    """admin: {ids, status?|priority?} or {ids, delete: true} in one go."""
    try:
        profile_id, is_admin = _caller()
        body = request.get_json(silent=True) or {}
        payload, code = issue_service.bulk(_side(), actor=profile_id, actor_name=_actor_name(),
                                           is_admin=is_admin, ids=body.get('ids'), body=body,
                                           delete=get_database().delete_issue)
        return jsonify(payload), code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>/comments', methods=['POST'])
def add_issue_comment(issue_id):
    """reply on an issue: its reporter, a follower, or an admin."""
    try:
        profile_id, is_admin = _caller()
        payload, code = issue_service.comment(_side(), actor=profile_id, actor_name=_actor_name(),
                                              is_admin=is_admin, issue_id=issue_id,
                                              body=request.get_json(silent=True) or {})
        return jsonify(payload), code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _build_issue_snapshot(database, entity_type, entity_id):
    """Capture current state of the entity for the issue report."""
    snapshot = {}
    try:
        conn = database._get_connection()
        cursor = conn.cursor()

        if entity_type == 'track':
            cursor.execute("""
                SELECT t.id, t.title, t.track_number, t.duration,
                       t.file_path, t.bitrate, t.bpm,
                       t.spotify_track_id, t.musicbrainz_recording_id, t.deezer_id as track_deezer_id,
                       a.name as artist_name, a.id as artist_id,
                       a.spotify_artist_id, a.musicbrainz_id as artist_musicbrainz_id,
                       a.deezer_id as artist_deezer_id, a.tidal_id as artist_tidal_id,
                       a.qobuz_id as artist_qobuz_id, a.thumb_url as artist_thumb,
                       al.title as album_title, al.year, al.thumb_url as album_thumb,
                       al.id as album_id, al.spotify_album_id, al.musicbrainz_release_id,
                       al.deezer_id as album_deezer_id, al.tidal_id as album_tidal_id,
                       al.qobuz_id as album_qobuz_id, al.label, al.record_type,
                       al.track_count as album_track_count
                FROM tracks t
                JOIN artists a ON t.artist_id = a.id
                JOIN albums al ON t.album_id = al.id
                WHERE t.id = ?
            """, (entity_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                # Add format info if file exists
                resolved = _resolve_library_file_path(d.get('file_path'))
                if resolved:
                    ext = os.path.splitext(resolved)[1].lower().lstrip('.')
                    d['format'] = ext.upper()
                    d['quality'] = _get_audio_quality_string(resolved)
                # Fix Plex/Jellyfin relative thumb URLs
                if d.get('artist_thumb'):
                    d['artist_thumb'] = fix_artist_image_url(d['artist_thumb']) or d['artist_thumb']
                if d.get('album_thumb'):
                    d['album_thumb'] = fix_artist_image_url(d['album_thumb']) or d['album_thumb']
                snapshot = d

        elif entity_type == 'album':
            cursor.execute("""
                SELECT al.id, al.title, al.year, al.track_count, al.thumb_url,
                       al.genres, al.label, al.record_type, al.duration,
                       al.spotify_album_id, al.musicbrainz_release_id,
                       al.deezer_id as album_deezer_id, al.tidal_id as album_tidal_id,
                       al.qobuz_id as album_qobuz_id, al.upc,
                       a.name as artist_name, a.id as artist_id,
                       a.spotify_artist_id, a.musicbrainz_id as artist_musicbrainz_id,
                       a.deezer_id as artist_deezer_id, a.tidal_id as artist_tidal_id,
                       a.qobuz_id as artist_qobuz_id, a.thumb_url as artist_thumb
                FROM albums al
                JOIN artists a ON al.artist_id = a.id
                WHERE al.id = ?
            """, (entity_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                # Fix Plex/Jellyfin relative thumb URLs
                if d.get('thumb_url'):
                    d['thumb_url'] = fix_artist_image_url(d['thumb_url']) or d['thumb_url']
                if d.get('artist_thumb'):
                    d['artist_thumb'] = fix_artist_image_url(d['artist_thumb']) or d['artist_thumb']
                # Parse genres
                if d.get('genres'):
                    try:
                        d['genres'] = json.loads(d['genres'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Get track listing with enriched data
                cursor.execute("""
                    SELECT id, title, track_number, duration, file_path, bitrate,
                           spotify_track_id, bpm
                    FROM tracks WHERE album_id = ? ORDER BY track_number
                """, (entity_id,))
                tracks_list = []
                for r in cursor.fetchall():
                    td = dict(r)
                    # Add format from file extension
                    if td.get('file_path'):
                        resolved = _resolve_library_file_path(td['file_path'])
                        if resolved:
                            ext = os.path.splitext(resolved)[1].lower().lstrip('.')
                            td['format'] = ext.upper()
                    tracks_list.append(td)
                d['tracks'] = tracks_list
                snapshot = d

        elif entity_type == 'artist':
            cursor.execute("""
                SELECT id, name, thumb_url, genres, summary,
                       spotify_artist_id, musicbrainz_id as artist_musicbrainz_id,
                       deezer_id as artist_deezer_id, tidal_id as artist_tidal_id,
                       qobuz_id as artist_qobuz_id
                FROM artists WHERE id = ?
            """, (entity_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                # Fix Plex/Jellyfin relative thumb URL
                if d.get('thumb_url'):
                    d['thumb_url'] = fix_artist_image_url(d['thumb_url']) or d['thumb_url']
                if d.get('genres'):
                    try:
                        d['genres'] = json.loads(d['genres'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                snapshot = d

    except Exception as e:
        logger.error(f"Error building issue snapshot: {e}")
        snapshot['_snapshot_error'] = str(e)

    return snapshot
