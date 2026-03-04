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

        try:
            from database.music_database import get_database
            db = get_database()
            raw_tracks = db.get_wishlist_tracks(profile_id=profile_id)

            # Category filter
            if category in ("singles", "albums"):
                raw_tracks = [
                    t for t in raw_tracks
                    if _track_category(t) == category
                ]

            total = len(raw_tracks)
            start = (page - 1) * limit
            tracks = raw_tracks[start:start + limit]

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

        Body: {"spotify_track_data": {...}, "failure_reason": "...", "source_type": "..."}
        """
        body = request.get_json(silent=True) or {}
        track_data = body.get("spotify_track_data")
        reason = body.get("failure_reason", "Added via API")
        source_type = body.get("source_type", "api")
        profile_id = parse_profile_id(request)

        if not track_data:
            return api_error("BAD_REQUEST", "Missing 'spotify_track_data' in body.", 400)

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
        """Remove a track from the wishlist by its Spotify track ID."""
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


def _track_category(track):
    """Determine if a wishlist track is a single or album track."""
    album_type = ""
    if isinstance(track, dict):
        sd = track.get("spotify_data", {})
        if isinstance(sd, dict):
            album = sd.get("album", {})
            if isinstance(album, dict):
                album_type = album.get("album_type", "")
    return "albums" if album_type == "album" else "singles"
