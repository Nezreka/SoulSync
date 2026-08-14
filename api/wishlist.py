"""
Wishlist endpoints — view, add, remove, and trigger processing.
"""

from flask import request
from core.api_validation import parse_strict_int
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

        Body: {"track_data": {...}, "failure_reason": "...", "source_type": "...",
               "quality_profile_id": 7}

        ``quality_profile_id`` is the durable acquisition intent this item will
        be downloaded and imported against. Omitting it uses the app-wide
        default; sending an unknown id is a 400, never a silent fall back
        (P1-02/P2-04). For a track already on the wishlist an explicit id is
        authoritative and overwrites the stored one.

        ``201`` a new row was created. ``200`` an existing row was updated —
        both carry the stored ``track`` so the client can verify what landed,
        and ``created`` says which happened. ``409`` means nothing was written
        (R2-04).
        """
        body = request.get_json(silent=True) or {}
        track_data = body.get("track_data") or body.get("spotify_track_data")
        reason = body.get("failure_reason", "Added via API")
        source_type = body.get("source_type", "api")
        profile_id = parse_profile_id(request)

        if not track_data or not isinstance(track_data, dict):
            return api_error("BAD_REQUEST", "Missing 'track_data' in body.", 400)

        try:
            from database.music_database import get_database
            db = get_database()

            quality_profile_id = None
            if body.get("quality_profile_id") is not None:
                quality_profile_id = parse_strict_int(body.get("quality_profile_id"))
                if quality_profile_id is None or quality_profile_id <= 0:
                    return api_error(
                        "BAD_REQUEST",
                        "quality_profile_id must be a positive integer.",
                        400,
                    )
                if not db.quality_profile_exists(quality_profile_id):
                    return api_error("BAD_REQUEST", "Unknown quality_profile_id.", 400)

            outcome = db.add_to_wishlist_detailed(
                track_data,
                failure_reason=reason,
                source_type=source_type,
                profile_id=profile_id,
                quality_profile_id=quality_profile_id,
                # An API caller asking for a track IS explicit user intent, so it
                # bypasses the ignore-list gate and updates an existing row
                # authoritatively rather than being silently dropped.
                user_initiated=True,
            )
            if outcome["applied"]:
                # dd28-12: mirror the intent BACK into lib2. Without this the
                # hourly reconciler saw a wishlisted track with no lib2 rule
                # making it wanted, pruned it, and the entry vanished within
                # the hour — so failed downloads silently stopped retrying.
                from core.settings import config_manager
                from core.library2.monitor_sync import sync_wishlist_addition
                sync_wishlist_addition(
                    db, config_manager,
                    [{"track_data": track_data,
                      "source_info": {"lib2_track_id": (track_data or {}).get("lib2_track_id")}
                      if isinstance(track_data, dict) else {}}],
                    profile_id=profile_id,
                )
                # Read back by the key that was actually written: a second album
                # for the same track is stored as ``<id>::<album>``, so looking
                # the bare id up again returns the OTHER album's row (R2-09).
                stored = db.get_wishlist_track(
                    outcome.get("track_id") or track_data.get("id"), profile_id=profile_id
                )
                # An existing row that was authoritatively refreshed is a
                # success, not a conflict — the endpoint promises the explicit
                # profile overwrites the stored one, so it must report that it
                # did and let the client read the result back (R2-04).
                created = outcome["status"] == "created"
                return api_success(
                    {
                        "message": (
                            "Track added to wishlist." if created
                            else "Track already on the wishlist; entry updated."
                        ),
                        "created": created,
                        "track": serialize_wishlist_track(stored) if stored else None,
                    },
                    status=201 if created else 200,
                )
            if outcome["status"] == "rejected":
                return api_error("BAD_REQUEST", outcome.get("reason") or "Track rejected.", 400)
            return api_error(
                "CONFLICT",
                outcome.get("reason") or "Track may already be in wishlist.",
                409,
            )
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
            descriptors = [
                row for row in db.get_wishlist_tracks(profile_id=profile_id)
                if (
                    str(row.get("spotify_track_id") or "") == str(track_id)
                    if "::" in str(track_id)
                    else str(row.get("spotify_track_id") or "").split("::", 1)[0]
                    == str(track_id).split("::", 1)[0]
                )
            ]
            ok = db.remove_from_wishlist(track_id, profile_id=profile_id)
            if ok:
                from core.settings import config_manager
                from core.library2.monitor_sync import sync_wishlist_removal
                sync_wishlist_removal(
                    db, config_manager, descriptors, profile_id=profile_id,
                )
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
