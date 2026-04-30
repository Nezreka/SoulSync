"""
Wishlist endpoints — view, add, remove, and trigger processing.
"""

from flask import request
from .auth import require_api_key
from .helpers import api_success, api_error, parse_pagination, build_pagination, parse_fields, parse_profile_id
from .serializers import serialize_wishlist_track


def register_routes(bp):

    @bp.route("/wishlist", methods=["GET"])
    @require_api_key
    def list_wishlist():
        """List wishlist tracks with optional category filter and standardized format."""
        category = request.args.get("category")  # "singles" or "albums"
        page, limit = parse_pagination(request)
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        category_filter = category if category in ("singles", "albums") else None

        try:
            from database.music_database import get_database
            db = get_database()
            offset = (page - 1) * limit
            tracks = db.get_wishlist_tracks(
                profile_id=profile_id,
                category=category_filter,
                limit=limit,
                offset=offset,
            )
            total = db.get_wishlist_count(profile_id=profile_id, category=category_filter)

            return api_success(
                {"tracks": [serialize_wishlist_track(t, fields) for t in tracks]},
                pagination=build_pagination(page, limit, total),
            )
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)

    @bp.route("/wishlist", methods=["POST"])
    @require_api_key
    def add_to_wishlist():
        """Add a track to the wishlist.

        Body: {"track_data": {...}, "failure_reason": "...", "source_type": "..."}
        """
        body = request.get_json(silent=True) or {}
        track_data = body.get("track_data") or body.get("spotify_track_data")
        reason = body.get("failure_reason", "Added via API")
        source_type = body.get("source_type", "api")
        profile_id = parse_profile_id(request)

        if not track_data:
            return api_error("BAD_REQUEST", "Missing 'track_data' in body.", 400)

        try:
            from database.music_database import get_database
            db = get_database()
            ok = db.add_to_wishlist(
                track_data,
                failure_reason=reason,
                source_type=source_type,
                profile_id=profile_id,
            )
            if ok:
                return api_success({"message": "Track added to wishlist."}, status=201)
            return api_error("CONFLICT", "Track may already be in wishlist.", 409)
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)

    @bp.route("/wishlist/<track_id>", methods=["DELETE"])
    @require_api_key
    def remove_from_wishlist(track_id):
        """Remove a track from the wishlist by its track ID."""
        profile_id = parse_profile_id(request)
        try:
            from database.music_database import get_database
            db = get_database()
            ok = db.remove_from_wishlist(track_id, profile_id=profile_id)
            if ok:
                return api_success({"message": "Track removed from wishlist."})
            return api_error("NOT_FOUND", "Track not found in wishlist.", 404)
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)

    @bp.route("/wishlist/process", methods=["POST"])
    @require_api_key
    def process_wishlist():
        """Trigger wishlist download processing."""
        try:
            from web_server import is_wishlist_actually_processing
            if is_wishlist_actually_processing():
                return api_error("CONFLICT", "Wishlist processing is already running.", 409)

            from web_server import start_wishlist_missing_downloads
            start_wishlist_missing_downloads()
            return api_success({"message": "Wishlist processing started."})
        except ImportError:
            return api_error("NOT_AVAILABLE", "Wishlist processing function not available.", 501)
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)
