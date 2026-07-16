"""Video backup management API (arr-parity P10) — admin-only (the blueprint
gate covers the prefix; restore is as sensitive as it gets)."""

from __future__ import annotations

from flask import jsonify, request, send_file

from utils.logging_config import get_logger

logger = get_logger("video_api.backups")


def register_routes(bp):
    @bp.route("/backups", methods=["GET"])
    def video_backups_list():
        from core.video.backup_restore import list_backups, pending_restore
        return jsonify({"success": True, "backups": list_backups(),
                        "pending_restore": pending_restore()})

    @bp.route("/backups", methods=["POST"])
    def video_backups_create():
        from core.video.backup_restore import create_now
        res = create_now()
        if not res.get("ok"):
            return jsonify({"success": False, "error": res.get("error")}), 500
        return jsonify({"success": True, "name": res["name"], "size_bytes": res["size_bytes"]})

    @bp.route("/backups/restore", methods=["POST"])
    def video_backups_restore():
        """Stage a backup — the swap happens on the next restart, and the
        current database is set aside (kept) rather than deleted."""
        from core.video.backup_restore import stage_restore
        body = request.get_json(silent=True) or {}
        res = stage_restore(body.get("name"))
        if not res.get("ok"):
            return jsonify({"success": False, "error": res.get("error")}), 400
        return jsonify({"success": True, "pending": res["pending"]})

    @bp.route("/backups/restore", methods=["DELETE"])
    def video_backups_restore_cancel():
        from core.video.backup_restore import cancel_restore
        if not cancel_restore():
            return jsonify({"success": False, "error": "No restore is staged."}), 404
        return jsonify({"success": True})

    @bp.route("/backups/<name>/download", methods=["GET"])
    def video_backups_download(name):
        from core.video.backup_restore import _resolve
        p = _resolve(name)
        if not p:
            return jsonify({"error": "Unknown backup."}), 404
        return send_file(p, as_attachment=True, download_name=name)
