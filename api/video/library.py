"""Video library listing endpoint.

GET /api/video/library -> {"movies": [...], "shows": [...]}
Reads what the last scan mirrored from the media server into video.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library")


def register_routes(bp):
    @bp.route("/library", methods=["GET"])
    def video_library():
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify(get_video_db().query_library(
                request.args.get("kind", "movies"),
                search=request.args.get("search") or None,
                letter=request.args.get("letter") or None,
                sort=request.args.get("sort", "title"),
                status=request.args.get("status", "all"),
                page=request.args.get("page", 1),
                limit=request.args.get("limit", 75),
                server_source=resolve_video_server(),
            ))
        except Exception:
            logger.exception("Failed to query video library")
            return jsonify({"error": "Failed to load video library"}), 500
