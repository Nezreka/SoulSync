"""Video dashboard endpoint — live counts from video.db.

GET /api/video/dashboard -> {library:{...}, downloads:{...}, watchlist, wishlist}
With an empty database every value is a real 0.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.dashboard")


def register_routes(bp):
    @bp.route("/health", methods=["GET"])
    def video_health():
        """Aggregated system health strip (library roots, disk space, recycle
        folder, maintenance errors, monitor liveness). Cheap + local only."""
        from . import get_video_db
        try:
            from core.video.health import collect
            return jsonify(collect(get_video_db()))
        except Exception:
            logger.exception("video health failed")
            return jsonify({"status": "ok", "checks": []})

    @bp.route("/dashboard", methods=["GET"])
    def video_dashboard():
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            server = resolve_video_server()                 # the VIDEO server, not music's active
            db = get_video_db()
            stats = db.dashboard_stats(server_source=server)
            stats["server"] = server
            stats["recent"] = db.recently_added(server_source=server, limit=20)
            return jsonify(stats)
        except Exception:
            logger.exception("Failed to build video dashboard stats")
            return jsonify({"error": "Failed to load video dashboard stats"}), 500
