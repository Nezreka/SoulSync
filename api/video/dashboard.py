"""Video dashboard endpoint — live counts from video.db.

GET /api/video/dashboard -> {library:{...}, downloads:{...}, watchlist, wishlist}
With an empty database every value is a real 0.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.dashboard")


def register_routes(bp):
    @bp.route("/dashboard", methods=["GET"])
    def video_dashboard():
        from . import get_video_db
        try:
            stats = get_video_db().dashboard_stats()
            try:
                from core.video.sources import resolve_video_server
                stats["server"] = resolve_video_server()  # the VIDEO server, not music's active
            except Exception:
                stats["server"] = None
            return jsonify(stats)
        except Exception:
            logger.exception("Failed to build video dashboard stats")
            return jsonify({"error": "Failed to load video dashboard stats"}), 500
