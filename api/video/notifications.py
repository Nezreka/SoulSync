"""Notification connections API (arr-parity P11) — admin-only (the GETs return
webhook URLs and bot tokens; the blueprint gate covers the whole prefix)."""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.notifications")


def register_routes(bp):
    @bp.route("/notifications", methods=["GET"])
    def video_notifications_list():
        from core.video.notifications import EVENTS, load_connections

        from . import get_video_db
        return jsonify({"connections": load_connections(get_video_db()),
                        "events": list(EVENTS)})

    @bp.route("/notifications", methods=["POST"])
    def video_notifications_save():
        from core.video.notifications import save_connection

        from . import get_video_db
        conn = save_connection(get_video_db(), request.get_json(silent=True) or {})
        if not conn:
            return jsonify({"success": False,
                            "error": "A connection needs a valid type + target "
                                     "(URL, or token + chat id for Telegram)."}), 400
        return jsonify({"success": True, **conn})

    @bp.route("/notifications/<int:conn_id>", methods=["DELETE"])
    def video_notifications_delete(conn_id):
        from core.video.notifications import delete_connection

        from . import get_video_db
        if not delete_connection(get_video_db(), conn_id):
            return jsonify({"success": False, "error": "Unknown connection."}), 404
        return jsonify({"success": True})

    @bp.route("/notifications/test", methods=["POST"])
    def video_notifications_test():
        """Fire a test message at the posted (possibly unsaved) config."""
        from core.video.notifications import test_connection
        ok = test_connection(request.get_json(silent=True) or {})
        if not ok:
            return jsonify({"success": False,
                            "error": "Test failed — check the URL/token and the server logs."}), 502
        return jsonify({"success": True})
