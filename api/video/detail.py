"""Video detail payloads (drill-in pages).

GET /api/video/detail/show/<id>   → show + seasons→episodes tree (owned roll-ups)
GET /api/video/detail/movie/<id>  → movie + owned/file info

Reads only video.db; isolated from the music API.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.detail")


def register_routes(bp):
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
