"""
Watchlist endpoints — view, add, remove, update watched artists, trigger scans.
"""

from flask import request, current_app
from core.api_validation import parse_strict_bool, parse_strict_id, parse_strict_int
from core.watchlist_sources import (
    ARTIST_ID_COLUMNS, SOURCE_COLUMNS, artist_id_match_sql, normalize_source,
)
from database.music_database import get_database
from utils.logging_config import get_logger
from .auth import require_api_key
from .helpers import api_success, api_error, parse_fields, parse_profile_id
from .serializers import serialize_watchlist_artist

logger = get_logger("api.watchlist")


def _parse_quality_profile_id(db, body):
    """``(value, error_response)`` for an optional ``quality_profile_id``.

    Three distinct states, per the P1-02/P2-04 contract: absent -> ``None`` and
    the caller keeps its default; present and valid -> the int; present and
    junk/unknown -> a 400, never a silent fall back to the default profile.
    """
    if "quality_profile_id" not in body:
        return None, None
    raw = body.get("quality_profile_id")
    if raw is None:
        return None, None
    parsed = parse_strict_int(raw)
    if parsed is None or parsed <= 0:
        return None, api_error(
            "BAD_REQUEST", "quality_profile_id must be a positive integer.", 400
        )
    if not db.quality_profile_exists(parsed):
        return None, api_error("BAD_REQUEST", "Unknown quality_profile_id.", 400)
    return parsed, None


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

        Body: {"artist_id": "...", "artist_name": "...", "source": "deezer",
               "quality_profile_id": 7}

        ``source`` names the provider explicitly. Omitting it falls back to the
        legacy id-shape guess, which cannot tell a numeric Deezer id from an
        iTunes one — native clients should always send it (P1-05).
        """
        body = request.get_json(silent=True) or {}
        artist_id = parse_strict_id(body.get("artist_id"))
        artist_name = body.get("artist_name")
        artist_name = artist_name.strip() if isinstance(artist_name, str) else None
        profile_id = parse_profile_id(request)

        if not artist_id or not artist_name:
            return api_error("BAD_REQUEST", "Missing 'artist_id' or 'artist_name'.", 400)

        source = None
        if body.get("source") is not None:
            source = normalize_source(body.get("source"))
            if source is None:
                return api_error(
                    "BAD_REQUEST",
                    f"Unknown source. Supported: {', '.join(sorted(SOURCE_COLUMNS))}.",
                    400,
                )

        try:
            db = get_database()
            quality_profile_id, error = _parse_quality_profile_id(db, body)
            if error:
                return error
            ok = db.add_artist_to_watchlist(
                artist_id,
                artist_name,
                profile_id=profile_id,
                source=source,
                quality_profile_id=quality_profile_id,
            )
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
            # §69.1 reverse edge: capture identity before delete, demonitor the
            # matching lib2 artist afterwards (both-way sync).
            descriptor = db.get_watchlist_artist_descriptor(artist_id, profile_id=profile_id)
            ok = db.remove_artist_from_watchlist(artist_id, profile_id=profile_id)
            if ok:
                try:
                    from core.settings import config_manager
                    from core.library2.monitor_sync import sync_watchlist_removal
                    sync_watchlist_removal(db, config_manager, descriptor,
                                           profile_id=profile_id)
                except Exception as sync_e:
                    logger.debug("watchlist reverse-sync skipped: %s", sync_e)
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
        # Only fields the client actually SENT are touched — this is a real
        # partial PATCH, unlike the legacy full-form endpoint (P2-03).
        updates = {}
        for key in allowed_fields & set(body):
            parsed = parse_strict_bool(body[key])
            if parsed is None:
                return api_error("BAD_REQUEST", f"{key} must be a boolean.", 400)
            updates[key] = parsed

        try:
            db = get_database()
            quality_profile_id, error = _parse_quality_profile_id(db, body)
            if error:
                return error
            if quality_profile_id is not None:
                updates["quality_profile_id"] = quality_profile_id

            if not updates:
                return api_error(
                    "BAD_REQUEST",
                    "No valid fields provided. Allowed: "
                    f"{', '.join(sorted(allowed_fields | {'quality_profile_id'}))}",
                    400,
                )

            with db._get_connection() as conn:
                cursor = conn.cursor()

                # Build SET clause
                set_parts = [f"{k} = ?" for k in updates]
                values = [
                    int(v) if key == "quality_profile_id" else int(bool(v))
                    for key, v in updates.items()
                ]

                cursor.execute(f"""
                    UPDATE watchlist_artists
                    SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP
                    WHERE {artist_id_match_sql()} AND profile_id = ?
                """, values + [artist_id] * len(ARTIST_ID_COLUMNS) + [profile_id])

                if cursor.rowcount > 0:
                    conn.commit()
                    return api_success({"message": "Watchlist settings updated.", "updated": updates})
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
