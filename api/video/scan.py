"""Video library scan endpoints.

POST /api/video/scan/request -> start a background scan of the active server
GET  /api/video/scan/status  -> current scan progress/state

The scan READS the media server (source of truth) into video.db. Triggering the
server's own rescan (post-download) is wired separately into the download flow.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.scan")


def register_routes(bp):
    @bp.route("/scan/request", methods=["POST"])
    def video_scan_request():
        from . import get_video_db
        from core.video.scanner import get_video_scanner
        from core.video.sources import get_active_video_source
        body = request.get_json(silent=True) or {}
        mode = body.get("mode", "full")
        # Which library to scan — movies and TV are independent libraries, so the
        # UI can target one or both. The scanner normalizes/validates it.
        media_type = body.get("media_type", "all")
        scanner = get_video_scanner(get_video_db())
        return jsonify(scanner.request_scan(get_active_video_source, mode, media_type))

    @bp.route("/scan/status", methods=["GET"])
    def video_scan_status():
        from . import get_video_db
        from core.video.scanner import get_video_scanner
        return jsonify(get_video_scanner(get_video_db()).get_status())

    @bp.route("/scan/stop", methods=["POST"])
    def video_scan_stop():
        from . import get_video_db
        from core.video.scanner import get_video_scanner
        return jsonify(get_video_scanner(get_video_db()).cancel())
