"""
Watchlist endpoints — view, add, remove watched artists, trigger scans.
"""

from flask import request, current_app
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/watchlist", methods=["GET"])
    @require_api_key
    def list_watchlist():
        """List all watchlist artists."""
        try:
            db = get_database()
            artists = db.get_watchlist_artists()
            return api_success({
                "artists": [_serialize_watchlist_artist(a) for a in artists]
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

        if not artist_id or not artist_name:
            return api_error("BAD_REQUEST", "Missing 'artist_id' or 'artist_name'.", 400)

        try:
            db = get_database()
            ok = db.add_artist_to_watchlist(artist_id, artist_name)
            if ok:
                return api_success({"message": f"Added {artist_name} to watchlist."}, status=201)
            return api_error("INTERNAL_ERROR", "Failed to add artist to watchlist.", 500)
        except Exception as e:
            return api_error("WATCHLIST_ERROR", str(e), 500)

    @bp.route("/watchlist/<artist_id>", methods=["DELETE"])
    @require_api_key
    def remove_from_watchlist(artist_id):
        """Remove an artist from the watchlist."""
        try:
            db = get_database()
            ok = db.remove_artist_from_watchlist(artist_id)
            if ok:
                return api_success({"message": "Artist removed from watchlist."})
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


def _serialize_watchlist_artist(a):
    return {
        "id": a.id,
        "spotify_artist_id": a.spotify_artist_id,
        "itunes_artist_id": a.itunes_artist_id,
        "artist_name": a.artist_name,
        "image_url": a.image_url,
        "date_added": a.date_added.isoformat() if a.date_added else None,
        "last_scan_timestamp": a.last_scan_timestamp.isoformat() if a.last_scan_timestamp else None,
        "include_albums": a.include_albums,
        "include_eps": a.include_eps,
        "include_singles": a.include_singles,
    }
