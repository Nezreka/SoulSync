"""Video detail payloads (drill-in pages).

GET /api/video/detail/show/<id>   → show + seasons→episodes tree (owned roll-ups)
GET /api/video/detail/movie/<id>  → movie + owned/file info

Reads only video.db; isolated from the music API.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.detail")


def register_routes(bp):
    @bp.route("/monitor", methods=["POST"])
    def video_set_monitor():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("id")
        if kind not in ("movie", "show") or not isinstance(item_id, int):
            return jsonify({"error": "bad request"}), 400
        ok = get_video_db().set_monitored(kind, item_id, bool(body.get("monitored")))
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True, "monitored": bool(body.get("monitored"))})

    @bp.route("/detail/show/<int:show_id>", methods=["GET"])
    def video_show_detail(show_id):
        from . import get_video_db
        data = get_video_db().show_detail(show_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/movie/<int:movie_id>", methods=["GET"])
    def video_movie_detail(movie_id):
        from . import get_video_db
        data = get_video_db().movie_detail(movie_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/show/<int:show_id>/refresh-art", methods=["POST"])
    def video_show_refresh_art(show_id):
        """Lazy on-view backfill: pull missing season posters / episode art from
        TMDB and cache them. Best-effort — never errors the page."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_show_art(show_id)
        except Exception:
            logger.exception("refresh-art failed for show %s", show_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)

    @bp.route("/detail/movie/<int:movie_id>/refresh-art", methods=["POST"])
    def video_movie_refresh_art(movie_id):
        """Lazy on-view backfill for a movie (cast / genres / backdrop / ratings)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_movie_art(movie_id)
        except Exception:
            logger.exception("refresh-art failed for movie %s", movie_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)
