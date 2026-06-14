"""Video library scan endpoints.

POST /api/video/scan/request -> start a background scan of the active server
GET  /api/video/scan/status  -> current scan progress/state

The scan READS the media server (source of truth) into video.db. Triggering the
server's own rescan (post-download) is wired separately into the download flow.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.scan")


def register_routes(bp):
    @bp.route("/scan/request", methods=["POST"])
    def video_scan_request():
        from . import get_video_db
        from core.video.scanner import get_video_scanner
        from core.video.sources import get_active_video_source
        scanner = get_video_scanner(get_video_db())
        return jsonify(scanner.request_scan(get_active_video_source))

    @bp.route("/scan/status", methods=["GET"])
    def video_scan_status():
        from . import get_video_db
        from core.video.scanner import get_video_scanner
        return jsonify(get_video_scanner(get_video_db()).get_status())
