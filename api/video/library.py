"""Video library listing endpoint.

GET /api/video/library -> {"movies": [...], "shows": [...]}
Reads what the last scan mirrored from the media server into video.db.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.library")


def register_routes(bp):
    @bp.route("/library", methods=["GET"])
    def video_library():
        from . import get_video_db
        try:
            db = get_video_db()
            return jsonify({"movies": db.list_movies(), "shows": db.list_shows()})
        except Exception:
            logger.exception("Failed to list video library")
            return jsonify({"error": "Failed to load video library"}), 500
