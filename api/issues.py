"""Music issues endpoints - lifted from web_server.py.

bodies byte-identical; only the decorator changed.
"""

import json
import os

from flask import Blueprint, jsonify, request

from core.metadata import normalize_image_url as fix_artist_image_url
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

@bp.route('/api/issues', methods=['GET'])
def list_issues():
    """List issues. Admin sees all; non-admin sees own only."""
    try:
        database = get_database()
        profile_id = request.headers.get('X-Profile-Id', '1')
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1

        # Determine admin status
        profile = database.get_profile(profile_id)
        is_admin = profile.get('is_admin', False) if profile else False

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
        # Fix Plex/Jellyfin relative thumb URLs in stored snapshots
        for issue in result.get('issues', []):
            snap = issue.get('snapshot_data')
            if isinstance(snap, dict):
                for key in ('thumb_url', 'artist_thumb', 'album_thumb'):
                    if snap.get(key):
                        snap[key] = fix_artist_image_url(snap[key]) or snap[key]
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues', methods=['POST'])
def create_issue():
    """Create a new library issue."""
    try:
        database = get_database()
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Use header for profile_id (not body) to prevent spoofing
        profile_id = request.headers.get('X-Profile-Id', '1')
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1
        entity_type = data.get('entity_type')
        entity_id = data.get('entity_id')
        category = data.get('category')
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        priority = data.get('priority', 'normal')

        if not entity_type or not entity_id or not category or not title:
            return jsonify({"success": False, "error": "entity_type, entity_id, category, and title are required"}), 400

        valid_types = ('artist', 'album', 'track')
        if entity_type not in valid_types:
            return jsonify({"success": False, "error": f"entity_type must be one of: {', '.join(valid_types)}"}), 400

        valid_categories = ('wrong_track', 'wrong_metadata', 'wrong_cover', 'duplicate_tracks',
                           'missing_tracks', 'audio_quality', 'wrong_artist', 'wrong_album',
                           'incomplete_album', 'other')
        if category not in valid_categories:
            return jsonify({"success": False, "error": f"Invalid category: {category}"}), 400

        # Build snapshot of the entity's current state
        snapshot = _build_issue_snapshot(database, entity_type, str(entity_id))

        result = database.create_issue(
            profile_id=profile_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            category=category,
            title=title,
            description=description,
            snapshot_data=snapshot,
            priority=priority,
        )
        return jsonify(result), 201 if result.get('success') else 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['GET'])
def get_issue(issue_id):
    """Get a single issue."""
    try:
        database = get_database()
        issue = database.get_issue(issue_id)
        if not issue:
            return jsonify({"success": False, "error": "Issue not found"}), 404
        # Fix Plex/Jellyfin relative thumb URLs in stored snapshot
        snap = issue.get('snapshot_data')
        if isinstance(snap, dict):
            for key in ('thumb_url', 'artist_thumb', 'album_thumb'):
                if snap.get(key):
                    snap[key] = fix_artist_image_url(snap[key]) or snap[key]
        return jsonify({"success": True, "issue": issue})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['PUT'])
def update_issue(issue_id):
    """Update an issue (admin: respond/resolve; user: edit own description)."""
    try:
        database = get_database()
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        profile_id = request.headers.get('X-Profile-Id', '1')
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1

        profile = database.get_profile(profile_id)
        is_admin = profile.get('is_admin', False) if profile else False

        # Non-admin can only edit their own issue's title/description
        if not is_admin:
            issue = database.get_issue(issue_id)
            if not issue:
                return jsonify({"success": False, "error": "Issue not found"}), 404
            if issue['profile_id'] != profile_id:
                return jsonify({"success": False, "error": "Not authorized"}), 403
            data = {k: v for k, v in data.items() if k in ('title', 'description')}

        # If resolving, stamp resolved_by and resolved_at
        if data.get('status') in ('resolved', 'dismissed') and is_admin:
            data['resolved_by'] = profile_id
            from datetime import datetime
            data['resolved_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        # If reopening, clear resolution metadata
        elif data.get('status') in ('open', 'in_progress') and is_admin:
            data['resolved_by'] = None
            data['resolved_at'] = None

        result = database.update_issue(issue_id, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/<int:issue_id>', methods=['DELETE'])
def delete_issue(issue_id):
    """Delete an issue (admin or issue owner)."""
    try:
        database = get_database()
        profile_id = request.headers.get('X-Profile-Id', '1')
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1

        profile = database.get_profile(profile_id)
        is_admin = profile.get('is_admin', False) if profile else False

        if not is_admin:
            issue = database.get_issue(issue_id)
            if not issue:
                return jsonify({"success": False, "error": "Issue not found"}), 404
            if issue['profile_id'] != profile_id:
                return jsonify({"success": False, "error": "Not authorized"}), 403

        result = database.delete_issue(issue_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/issues/counts', methods=['GET'])
def get_issue_counts():
    """Get issue counts by status for badge display."""
    try:
        database = get_database()
        profile_id = request.headers.get('X-Profile-Id', '1')
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1
        profile = database.get_profile(profile_id)
        is_admin = profile.get('is_admin', False) if profile else False
        counts = database.get_issue_counts(is_admin=is_admin, profile_id=profile_id)
        return jsonify({"success": True, "counts": counts})
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
                       f.path AS file_path, f.bitrate, t.bpm,
                       t.spotify_id AS spotify_track_id,
                       t.musicbrainz_id AS musicbrainz_recording_id,
                       json_extract(t.external_ids, '$.deezer') as track_deezer_id,
                       a.name as artist_name, a.id as artist_id,
                       a.spotify_id AS spotify_artist_id,
                       a.musicbrainz_id as artist_musicbrainz_id,
                       json_extract(a.external_ids, '$.deezer') as artist_deezer_id,
                       json_extract(a.external_ids, '$.tidal') as artist_tidal_id,
                       json_extract(a.external_ids, '$.qobuz') as artist_qobuz_id,
                       a.image_url as artist_thumb,
                       al.title as album_title, al.year, al.image_url as album_thumb,
                       al.id as album_id, al.spotify_id AS spotify_album_id,
                       al.musicbrainz_id AS musicbrainz_release_id,
                       json_extract(al.external_ids, '$.deezer') as album_deezer_id,
                       json_extract(al.external_ids, '$.tidal') as album_tidal_id,
                       json_extract(al.external_ids, '$.qobuz') as album_qobuz_id,
                       al.label, al.album_type AS record_type,
                       al.track_count as album_track_count
                FROM lib2_tracks t
                JOIN lib2_albums al ON al.id = t.album_id
                JOIN lib2_artists a ON a.id = al.primary_artist_id
                LEFT JOIN lib2_track_files f
                       ON f.track_id = t.id AND f.is_primary = 1
                      AND COALESCE(f.file_state, 'active') <> 'deleted'
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
                SELECT al.id, al.title, al.year, al.track_count,
                       al.image_url AS thumb_url, al.genres, al.label,
                       al.album_type AS record_type, al.duration,
                       al.spotify_id AS spotify_album_id,
                       al.musicbrainz_id AS musicbrainz_release_id,
                       json_extract(al.external_ids, '$.deezer') as album_deezer_id,
                       json_extract(al.external_ids, '$.tidal') as album_tidal_id,
                       json_extract(al.external_ids, '$.qobuz') as album_qobuz_id, al.upc,
                       a.name as artist_name, a.id as artist_id,
                       a.spotify_id AS spotify_artist_id,
                       a.musicbrainz_id as artist_musicbrainz_id,
                       json_extract(a.external_ids, '$.deezer') as artist_deezer_id,
                       json_extract(a.external_ids, '$.tidal') as artist_tidal_id,
                       json_extract(a.external_ids, '$.qobuz') as artist_qobuz_id,
                       a.image_url as artist_thumb
                FROM lib2_albums al
                JOIN lib2_artists a ON a.id = al.primary_artist_id
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
                    SELECT t.id, t.title, t.track_number, t.duration,
                           f.path AS file_path, f.bitrate,
                           t.spotify_id AS spotify_track_id, t.bpm
                    FROM lib2_tracks t
                    LEFT JOIN lib2_track_files f
                           ON f.track_id = t.id AND f.is_primary = 1
                          AND COALESCE(f.file_state, 'active') <> 'deleted'
                    WHERE t.album_id = ? ORDER BY t.track_number
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
                SELECT id, name, image_url AS thumb_url, genres, summary,
                       spotify_id AS spotify_artist_id,
                       musicbrainz_id as artist_musicbrainz_id,
                       json_extract(external_ids, '$.deezer') as artist_deezer_id,
                       json_extract(external_ids, '$.tidal') as artist_tidal_id,
                       json_extract(external_ids, '$.qobuz') as artist_qobuz_id
                FROM lib2_artists WHERE id = ?
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
