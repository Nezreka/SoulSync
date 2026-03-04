"""
Watchlist endpoints — view, add, remove, update watched artists, trigger scans.
"""

from flask import request, current_app
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, parse_fields, parse_profile_id
from .serializers import serialize_watchlist_artist


def register_routes(bp):

    @bp.route("/watchlist", methods=["GET"])
    @require_api_key
    def list_watchlist():
        """List all watchlist artists for the current profile."""
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)
        try:
            db = get_database()
            artists = db.get_watchlist_artists(profile_id=profile_id)
            return api_success({
                "artists": [serialize_watchlist_artist(a, fields) for a in artists]
            })
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)

    @bp.route("/watchlist", methods=["POST"])
    @require_api_key
    def add_to_watchlist():
        """Add an artist to the watchlist.

        Body: {"artist_id": "...", "artist_name": "..."}
        """
        body = request.get_json(silent=True) or {}
        artist_id = body.get("artist_id")
        artist_name = body.get("artist_name")
        profile_id = parse_profile_id(request)

        if not artist_id or not artist_name:
            return api_error("BAD_REQUEST", "Missing 'artist_id' or 'artist_name'.", 400)

        try:
            db = get_database()
            ok = db.add_artist_to_watchlist(artist_id, artist_name, profile_id=profile_id)
            if ok:
                return api_success({"message": f"Added {artist_name} to watchlist."}, status=201)
            return api_error("INTERNAL_ERROR", "Failed to add artist to watchlist.", 500)
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)

    @bp.route("/watchlist/<artist_id>", methods=["DELETE"])
    @require_api_key
    def remove_from_watchlist(artist_id):
        """Remove an artist from the watchlist."""
        profile_id = parse_profile_id(request)
        try:
            db = get_database()
            ok = db.remove_artist_from_watchlist(artist_id, profile_id=profile_id)
            if ok:
                return api_success({"message": "Artist removed from watchlist."})
            return api_error("NOT_FOUND", "Artist not found in watchlist.", 404)
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)

    @bp.route("/watchlist/<artist_id>", methods=["PATCH"])
    @require_api_key
    def update_watchlist_filters(artist_id):
        """Update content type filters for a watchlist artist.

        Body: {"include_albums": true, "include_live": false, ...}
        Accepts any combination of: include_albums, include_eps, include_singles,
        include_live, include_remixes, include_acoustic, include_compilations
        """
        body = request.get_json(silent=True) or {}
        profile_id = parse_profile_id(request)

        allowed_fields = {
            "include_albums", "include_eps", "include_singles",
            "include_live", "include_remixes", "include_acoustic", "include_compilations",
        }
        updates = {k: v for k, v in body.items() if k in allowed_fields}

        if not updates:
            return api_error("BAD_REQUEST", f"No valid filter fields provided. Allowed: {', '.join(sorted(allowed_fields))}", 400)

        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            # Build SET clause
            set_parts = [f"{k} = ?" for k in updates]
            values = [int(bool(v)) for v in updates.values()]

            cursor.execute(f"""
                UPDATE watchlist_artists
                SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP
                WHERE (spotify_artist_id = ? OR itunes_artist_id = ?) AND profile_id = ?
            """, values + [artist_id, artist_id, profile_id])

            if cursor.rowcount > 0:
                conn.commit()
                return api_success({"message": "Watchlist filters updated.", "updated": updates})
            return api_error("NOT_FOUND", "Artist not found in watchlist.", 404)
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)

    @bp.route("/watchlist/scan", methods=["POST"])
    @require_api_key
    def trigger_scan():
        """Trigger a watchlist scan for new releases."""
        try:
            from web_server import is_watchlist_actually_scanning
            if is_watchlist_actually_scanning():
                return api_error("CONFLICT", "Watchlist scan is already running.", 409)

            from web_server import start_watchlist_scan
            start_watchlist_scan()
            return api_success({"message": "Watchlist scan started."})
        except ImportError:
            return api_error("NOT_AVAILABLE", "Watchlist scan function not available.", 501)
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)
