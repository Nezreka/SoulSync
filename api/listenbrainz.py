"""
ListenBrainz endpoints — browse cached ListenBrainz playlists and tracks.
"""

import json
from flask import request
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, parse_pagination, build_pagination


def register_routes(bp):

    @bp.route("/listenbrainz/playlists", methods=["GET"])
    @require_api_key
    def list_listenbrainz_playlists():
        """List cached ListenBrainz playlists.

        Query params:
            type: Filter by playlist_type (e.g. 'weekly-jams', 'weekly-exploration')
            page: Page number
            limit: Items per page
        """
        page, limit = parse_pagination(request)
        playlist_type = request.args.get("type")

        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            where_parts = []
            params = []

            if playlist_type:
                where_parts.append("playlist_type = ?")
                params.append(playlist_type)

            where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

            # Count
            cursor.execute(f"SELECT COUNT(*) as cnt FROM listenbrainz_playlists {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            # Fetch page
            offset = (page - 1) * limit
            cursor.execute(f"""
                SELECT * FROM listenbrainz_playlists
                {where_clause}
                ORDER BY last_updated DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            playlists = []
            for row in cursor.fetchall():
                p = dict(row)
                # Parse annotation_data if it's JSON
                if p.get("annotation_data") and isinstance(p["annotation_data"], str):
                    try:
                        p["annotation_data"] = json.loads(p["annotation_data"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                playlists.append(p)

            return api_success(
                {"playlists": playlists},
                pagination=build_pagination(page, limit, total),
            )
        except Exception as e:
            return api_error("LISTENBRAINZ_ERROR", str(e), 500)

    @bp.route("/listenbrainz/playlists/<playlist_id>", methods=["GET"])
    @require_api_key
    def get_listenbrainz_playlist(playlist_id):
        """Get a ListenBrainz playlist with its tracks.

        playlist_id can be the internal ID or the MusicBrainz playlist MBID.
        """
        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            # Try by internal ID first, then by MBID
            try:
                int_id = int(playlist_id)
                cursor.execute("SELECT * FROM listenbrainz_playlists WHERE id = ?", (int_id,))
            except ValueError:
                cursor.execute("SELECT * FROM listenbrainz_playlists WHERE playlist_mbid = ?", (playlist_id,))

            row = cursor.fetchone()
            if not row:
                return api_error("NOT_FOUND", f"ListenBrainz playlist '{playlist_id}' not found.", 404)

            playlist = dict(row)
            if playlist.get("annotation_data") and isinstance(playlist["annotation_data"], str):
                try:
                    playlist["annotation_data"] = json.loads(playlist["annotation_data"])
                except (json.JSONDecodeError, TypeError):
                    pass

            # Get tracks
            cursor.execute("""
                SELECT * FROM listenbrainz_tracks
                WHERE playlist_id = ?
                ORDER BY position ASC
            """, (playlist["id"],))

            tracks = []
            for t_row in cursor.fetchall():
                track = dict(t_row)
                if track.get("additional_metadata") and isinstance(track["additional_metadata"], str):
                    try:
                        track["additional_metadata"] = json.loads(track["additional_metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                tracks.append(track)

            return api_success({
                "playlist": playlist,
                "tracks": tracks,
            })
        except Exception as e:
            return api_error("LISTENBRAINZ_ERROR", str(e), 500)
