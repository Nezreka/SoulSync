"""
Discovery endpoints — browse discovery pool, similar artists, and recent releases.
"""

from flask import request
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, parse_pagination, build_pagination, parse_fields, parse_profile_id
from .serializers import serialize_discovery_track, serialize_similar_artist, serialize_recent_release


def register_routes(bp):

    @bp.route("/discover/pool", methods=["GET"])
    @require_api_key
    def list_discovery_pool():
        """List discovery pool tracks with optional filters.

        Query params:
            new_releases_only: 'true' to filter to new releases (default: false)
            source: 'spotify' or 'itunes' (default: all)
            limit: max tracks (default: 100, max: 500)
            page: page number for pagination
        """
        page, limit = parse_pagination(request, default_limit=100, max_limit=500)
        new_releases_only = request.args.get("new_releases_only", "").lower() == "true"
        source = request.args.get("source")
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        if source and source not in ("spotify", "itunes"):
            return api_error("BAD_REQUEST", "source must be 'spotify' or 'itunes'.", 400)

        try:
            db = get_database()

            # Get total count for accurate pagination
            conn = db._get_connection()
            cursor = conn.cursor()
            count_wheres = ["profile_id = ?"]
            count_params = [profile_id]
            if new_releases_only:
                count_wheres.append("is_new_release = 1")
            if source:
                count_wheres.append("source = ?")
                count_params.append(source)
            cursor.execute(
                f"SELECT COUNT(*) as cnt FROM discovery_pool WHERE {' AND '.join(count_wheres)}",
                count_params,
            )
            total = cursor.fetchone()["cnt"]

            # Fetch page using offset/limit
            offset = (page - 1) * limit
            where_clauses = list(count_wheres)
            params = list(count_params)
            params.extend([limit, offset])
            cursor.execute(f"""
                SELECT * FROM discovery_pool
                WHERE {' AND '.join(where_clauses)}
                ORDER BY added_date DESC
                LIMIT ? OFFSET ?
            """, params)

            rows = cursor.fetchall()
            page_tracks = [dict(row) for row in rows]

            return api_success(
                {"tracks": [serialize_discovery_track(t, fields) for t in page_tracks]},
                pagination=build_pagination(page, limit, total),
            )
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)

    @bp.route("/discover/similar-artists", methods=["GET"])
    @require_api_key
    def list_similar_artists():
        """List top similar artists discovered from the watchlist.

        Query params:
            limit: max artists (default: 50, max: 200)
        """
        try:
            limit = min(200, max(1, int(request.args.get("limit", 50))))
        except (ValueError, TypeError):
            limit = 50
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            artists = db.get_top_similar_artists(limit=limit, profile_id=profile_id)
            return api_success({
                "artists": [serialize_similar_artist(a, fields) for a in artists]
            })
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)

    @bp.route("/discover/recent-releases", methods=["GET"])
    @require_api_key
    def list_recent_releases():
        """List recent releases from watched artists.

        Query params:
            limit: max releases (default: 50, max: 200)
        """
        try:
            limit = min(200, max(1, int(request.args.get("limit", 50))))
        except (ValueError, TypeError):
            limit = 50
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            releases = db.get_recent_releases(limit=limit, profile_id=profile_id)
            return api_success({
                "releases": [serialize_recent_release(r, fields) for r in releases]
            })
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)

    @bp.route("/discover/pool/metadata", methods=["GET"])
    @require_api_key
    def discovery_pool_metadata():
        """Get discovery pool metadata (last populated timestamp, track count)."""
        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_populated_timestamp, track_count, updated_at
                FROM discovery_pool_metadata
                WHERE profile_id = ?
            """, (profile_id,))
            row = cursor.fetchone()

            if not row:
                return api_success({
                    "last_populated": None,
                    "track_count": 0,
                    "updated_at": None,
                })

            return api_success({
                "last_populated": row["last_populated_timestamp"],
                "track_count": row["track_count"],
                "updated_at": row["updated_at"],
            })
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)

    # ── Bubble Snapshots ───────────────────────────────────────

    @bp.route("/discover/bubbles", methods=["GET"])
    @require_api_key
    def list_bubble_snapshots():
        """List all bubble snapshots for the current profile.

        Returns snapshots for all types: artist_bubbles, search_bubbles, discover_downloads.
        """
        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            result = {}
            for snap_type in ("artist_bubbles", "search_bubbles", "discover_downloads"):
                snapshot = db.get_bubble_snapshot(snap_type, profile_id=profile_id)
                result[snap_type] = snapshot  # None if not found

            return api_success({"snapshots": result})
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)

    @bp.route("/discover/bubbles/<snapshot_type>", methods=["GET"])
    @require_api_key
    def get_bubble_snapshot(snapshot_type):
        """Get a specific bubble snapshot by type.

        Types: artist_bubbles, search_bubbles, discover_downloads
        """
        valid_types = ("artist_bubbles", "search_bubbles", "discover_downloads")
        if snapshot_type not in valid_types:
            return api_error("BAD_REQUEST", f"type must be one of: {', '.join(valid_types)}", 400)

        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            snapshot = db.get_bubble_snapshot(snapshot_type, profile_id=profile_id)
            if not snapshot:
                return api_error("NOT_FOUND", f"No '{snapshot_type}' snapshot found.", 404)

            return api_success({"snapshot": snapshot})
        except Exception as e:
            return api_error("DISCOVER_ERROR", str(e), 500)
