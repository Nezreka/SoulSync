"""Live TV API (/api/live/*): sources, HDHomeRun discovery, channels, EPG,
and shared per-channel HLS streaming.

Admin-only: adding/removing/refreshing sources, discovering tuners, editing
channel visibility/names/EPG mapping.
All viewers with player access: listing enabled channels, EPG program window,
tuning into a shared live stream, and leaving.
"""

from __future__ import annotations

import os
import time

from flask import Blueprint, g, jsonify, request, send_from_directory

from core.permissions import requires_any
from core.player.live import hdhomerun, refresh, relay, store
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.live")

bp = Blueprint("live_tv", __name__)

VIEWER_SECTIONS = ("movies.access", "tv.access", "music.access")


def create_live_blueprint():
    return bp


def _db():
    from api.player import get_player_db
    return get_player_db()


def _viewer_id() -> str:
    pid = getattr(g, "profile_id", None) or 1
    sid = request.headers.get("X-SoulSync-Client-Id") or request.remote_addr or "web"
    return f"p{pid}:{sid}"


# ── admin: sources & discovery ───────────────────────────────────────────

@bp.route("/api/live/sources", methods=["GET"])
@admin_only
def live_sources_list():
    return jsonify({"success": True, "sources": store.list_sources(_db())})


@bp.route("/api/live/sources", methods=["POST"])
@admin_only
def live_sources_add():
    body = request.get_json(silent=True) or {}
    try:
        src = store.add_source(
            _db(),
            kind=body.get("kind", ""),
            name=body.get("name", ""),
            url=body.get("url", ""),
            tuner_count=body.get("tuner_count", 0),
            device_id=body.get("device_id"),
            device_auth=body.get("device_auth"),
            user_agent=body.get("user_agent"),
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
        )
    except store.LiveStoreError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    sync_result = None
    if body.get("refresh", True):
        sync_result = refresh.refresh_source(_db(), src["id"])
        src = store.get_source(_db(), src["id"]) or src
    return jsonify({"success": True, "source": src, "refresh": sync_result}), 201


@bp.route("/api/live/sources/<source_id>", methods=["DELETE"])
@admin_only
def live_sources_delete(source_id):
    ok = store.delete_source(_db(), source_id)
    if not ok:
        return jsonify({"success": False, "error": "Source not found."}), 404
    return jsonify({"success": True})


@bp.route("/api/live/sources/<source_id>/refresh", methods=["POST"])
@admin_only
def live_sources_refresh(source_id):
    res = refresh.refresh_source(_db(), source_id)
    code = 200 if res.get("ok") else 502
    return jsonify({"success": bool(res.get("ok")), **res}), code


@bp.route("/api/live/hdhomerun/discover", methods=["POST"])
@admin_only
def live_hdhomerun_discover():
    body = request.get_json(silent=True) or {}
    host = str(body.get("host") or "").strip()
    if host:
        try:
            dev = hdhomerun.probe_device(host)
            return jsonify({"success": True, "devices": [dev]})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"success": False, "error": f"Could not reach HDHomeRun at {host}: {exc}"}), 502
    devices = hdhomerun.discover_devices()
    return jsonify({"success": True, "devices": devices})


# ── channels & EPG ───────────────────────────────────────────────────────

@bp.route("/api/live/channels", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def live_channels_list():
    include_disabled = (request.args.get("all") == "1") and bool(getattr(g, "is_admin", False))
    chans = store.list_channels(_db(), include_disabled=include_disabled)
    return jsonify({"success": True, "channels": chans})


@bp.route("/api/live/channels/<channel_id>", methods=["PATCH"])
@admin_only
def live_channel_update(channel_id):
    body = request.get_json(silent=True) or {}
    ch = store.update_channel(_db(), channel_id, body)
    if not ch:
        return jsonify({"success": False, "error": "Channel not found."}), 404
    return jsonify({"success": True, "channel": ch})


@bp.route("/api/live/programs", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def live_programs_window():
    now = time.time()
    try:
        start_ts = float(request.args.get("start") or (now - 1800))
        end_ts = float(request.args.get("end") or (now + 6 * 3600))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "start and end must be unix timestamps"}), 400
    if end_ts <= start_ts:
        end_ts = start_ts + 6 * 3600
    programs = store.programs_window(_db(), start_ts, end_ts)
    return jsonify({"success": True, "start": start_ts, "end": end_ts, "programs": programs})


# ── tuning & shared HLS relay ────────────────────────────────────────────

@bp.route("/api/live/tune/<channel_id>", methods=["POST"])
@requires_any(*VIEWER_SECTIONS)
def live_tune(channel_id):
    ch = store.get_channel(_db(), channel_id)
    if not ch or not ch.get("enabled"):
        return jsonify({"success": False, "error": "Channel not available."}), 404
    try:
        session = relay.get_supervisor().join(ch, _viewer_id())
    except relay.TunerBusyError as exc:
        return jsonify({"success": False, "error": "tuner_busy",
                        "message": str(exc), "holders": exc.holders}), 503
    return jsonify({"success": True, "session": session, "channel": ch})


@bp.route("/api/live/leave/<channel_id>", methods=["POST"])
@requires_any(*VIEWER_SECTIONS)
def live_leave(channel_id):
    relay.get_supervisor().leave(channel_id, _viewer_id())
    return jsonify({"success": True})


@bp.route("/api/live/stream/<channel_id>/<path:filename>", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def live_stream_file(channel_id, filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"success": False, "error": "Invalid segment name."}), 400
    work_dir = relay.get_supervisor().touch(channel_id, _viewer_id())
    if not work_dir or not os.path.isdir(work_dir):
        return jsonify({"success": False, "error": "Channel session not running."}), 404
    target = os.path.join(work_dir, filename)
    if not os.path.isfile(target):
        return jsonify({"success": False, "error": "Segment not ready yet."}), 404
    resp = send_from_directory(work_dir, filename)
    if filename.endswith(".m3u8"):
        resp.headers["Content-Type"] = "application/vnd.apple.mpegurl"
        resp.headers["Cache-Control"] = "no-store"
    else:
        resp.headers["Cache-Control"] = "private, max-age=10"
    return resp
