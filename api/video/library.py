"""Video library listing endpoint.

GET /api/video/library -> {"movies": [...], "shows": [...]}
Reads what the last scan mirrored from the media server into video.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library")


def _capped_library(db, cap, server):
    """the library page for a kids profile: same filters and sort, over-cap
    and unrated titles left out, paged after the filter so counts and pages
    stay honest. walks the filtered set in big pages; only restricted
    profiles ever come through here."""
    from .kids import filter_library_items
    kind = request.args.get("kind", "movies")
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
        limit = max(1, min(500, int(request.args.get("limit", 75) or 75)))
    except (TypeError, ValueError):
        page, limit = 1, 75
    kept, p = [], 1
    while True:
        chunk = db.query_library(
            kind, search=request.args.get("search") or None,
            letter=request.args.get("letter") or None,
            sort=request.args.get("sort", "title"),
            status=request.args.get("status", "all"),
            genre=request.args.get("genre") or None,
            resolution=request.args.get("resolution") or None,
            page=p, limit=500, server_source=server, include_size=False)
        kept.extend(filter_library_items(db, chunk.get("items") or [], cap, kind))
        if not (chunk.get("pagination") or {}).get("has_next"):
            break
        p += 1
    total = len(kept)
    total_pages = max(1, (total + limit - 1) // limit)
    return {"items": kept[(page - 1) * limit: page * limit],
            "total_size_bytes": sum(int(it.get("size_bytes") or 0) for it in kept),
            "pagination": {"page": page, "total_pages": total_pages, "total_count": total,
                           "has_prev": page > 1, "has_next": page < total_pages}}


def register_routes(bp):
    @bp.route("/library", methods=["GET"])
    def video_library():
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            # Channels tab: followed YouTube channels (no media server involved —
            # ownership comes from the permanent download history).
            if request.args.get("kind") == "channels":
                return jsonify(get_video_db().query_channel_library(
                    search=request.args.get("search") or None,
                    letter=request.args.get("letter") or None,
                    sort=request.args.get("sort", "title"),
                    page=request.args.get("page", 1),
                    limit=request.args.get("limit", 75),
                ))
            from .kids import video_cap
            cap = video_cap()
            if cap is not None:
                return jsonify(_capped_library(get_video_db(), cap, resolve_video_server()))
            return jsonify(get_video_db().query_library(
                request.args.get("kind", "movies"),
                search=request.args.get("search") or None,
                letter=request.args.get("letter") or None,
                sort=request.args.get("sort", "title"),
                status=request.args.get("status", "all"),
                genre=request.args.get("genre") or None,
                resolution=request.args.get("resolution") or None,
                page=request.args.get("page", 1),
                limit=request.args.get("limit", 75),
                server_source=resolve_video_server(),
            ))
        except Exception:
            logger.exception("Failed to query video library")
            return jsonify({"error": "Failed to load video library"}), 500

    @bp.route("/library/resolutions", methods=["GET"])
    def video_library_resolutions():
        """File resolutions in use in the movie library — the library page's
        resolution filter dropdown (movies tab only)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"resolutions": get_video_db().library_resolutions(
                server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library resolutions")
            return jsonify({"resolutions": []})

    @bp.route("/library/genres", methods=["GET"])
    def video_library_genres():
        """Genre names in use for the given kind — the library filter dropdown.
        Read-only and ungated (unlike the collections field-suggestions endpoint)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"genres": get_video_db().library_genres(
                request.args.get("kind", "movies"), server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library genres")
            return jsonify({"genres": []})
