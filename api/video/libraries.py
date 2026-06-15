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
            from core.video.sources import list_video_libraries, resolve_video_server
            libs = list_video_libraries() or {"server": None, "movies": [], "tv": []}
            server = libs.get("server") or resolve_video_server()
            libs["selected"] = (get_video_db().get_library_selection(server)
                                if server else {"movies": None, "tv": None})
            return jsonify(libs)
        except Exception:
            logger.exception("Failed to list video libraries")
            return jsonify({"error": "Failed to list video libraries"}), 500

    @bp.route("/server", methods=["GET"])
    def video_server_status():
        """Which server the video side uses + which of Plex/Jellyfin are configured
        (so the UI can show a picker, or a 'connect a server' message)."""
        try:
            from core.video.sources import resolve_video_server
            from config.settings import config_manager
            plex = bool((config_manager.get_plex_config() or {}).get("base_url"))
            jelly = bool((config_manager.get_jellyfin_config() or {}).get("base_url"))
            return jsonify({"server": resolve_video_server(), "plex": plex, "jellyfin": jelly})
        except Exception:
            logger.exception("video server status failed")
            return jsonify({"server": None, "plex": False, "jellyfin": False})

    @bp.route("/server", methods=["POST"])
    def video_server_set():
        """Set the explicit video-side server pick (only meaningful when both Plex
        and Jellyfin are configured)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        choice = body.get("server")
        if choice not in ("plex", "jellyfin"):
            return jsonify({"error": "bad server"}), 400
        get_video_db().set_setting("video_server", choice)
        return jsonify({"status": "saved", "server": choice})

    @bp.route("/libraries", methods=["POST"])
    def save_video_libraries():
        from . import get_video_db
        try:
            from core.video.sources import resolve_video_server
            body = request.get_json(silent=True) or {}
            server = resolve_video_server()
            if not server:
                return jsonify({"error": "no video server"}), 400
            get_video_db().set_library_selection(server, body.get("movies"), body.get("tv"))
            return jsonify({"status": "saved", "server": server})
        except Exception:
            logger.exception("Failed to save video library selection")
            return jsonify({"error": "Failed to save selection"}), 500
