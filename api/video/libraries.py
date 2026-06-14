"""Video library mapping endpoints.

GET  /api/video/libraries -> discover the active server's Movies/TV libraries
                             + the user's current selection.
POST /api/video/libraries -> save {movies, tv} (library titles) for the active
                             server. The scanner then reads only those.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.libraries")


def register_routes(bp):
    @bp.route("/libraries", methods=["GET"])
    def video_libraries():
        from . import get_video_db
        try:
            from core.video.sources import list_video_libraries
            from config.settings import config_manager
            libs = list_video_libraries() or {"server": None, "movies": [], "tv": []}
            server = libs.get("server") or config_manager.get_active_media_server()
            libs["selected"] = (get_video_db().get_library_selection(server)
                                if server else {"movies": None, "tv": None})
            return jsonify(libs)
        except Exception:
            logger.exception("Failed to list video libraries")
            return jsonify({"error": "Failed to list video libraries"}), 500

    @bp.route("/libraries", methods=["POST"])
    def save_video_libraries():
        from . import get_video_db
        try:
            from config.settings import config_manager
            body = request.get_json(silent=True) or {}
            server = config_manager.get_active_media_server()
            get_video_db().set_library_selection(server, body.get("movies"), body.get("tv"))
            return jsonify({"status": "saved", "server": server})
        except Exception:
            logger.exception("Failed to save video library selection")
            return jsonify({"error": "Failed to save selection"}), 500
