"""Server activity (tautulli-replacement) endpoints - lifted from web_server.py.

bodies byte-identical; only the decorator changed.
"""

from flask import Blueprint, g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_activity = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"server_activity.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('server_activity', __name__)


def create_blueprint():
    return bp

@bp.route('/api/server-activity')
def get_server_activity():
    """Live Tautulli-style activity — every active Plex stream (music + video):
    who's playing what, direct play vs transcode, bandwidth, progress. App-wide;
    never raises (an unconfigured/down server is a normal state the UI shows)."""
    try:
        from core.server_activity import get_activity
        return jsonify(get_activity())
    except Exception:
        logger.exception("server activity failed")
        return jsonify({"ok": False, "reason": "error", "sessions": [],
                        "summary": {"streams": 0}})


@bp.route('/api/server-activity/history')
def get_server_activity_history():
    """Recent watch/listen history (Tautulli's History) — app-wide."""
    try:
        from core.server_activity import get_history
        limit = request.args.get("limit", default=40, type=int) or 40
        return jsonify(get_history(limit=limit))
    except Exception:
        logger.exception("server activity history failed")
        return jsonify({"ok": False, "reason": "error", "history": []})


@bp.route('/api/server-activity/stats')
def get_server_activity_stats():
    """Dashboard stats — most-watched, most-active users, plays over time, top
    devices (Tautulli's Statistics). App-wide; cached; never raises."""
    try:
        from core.server_activity import get_stats
        days = request.args.get("days", default=30, type=int) or 30
        return jsonify(get_stats(days=days))
    except Exception:
        logger.exception("server activity stats failed")
        return jsonify({"ok": False, "reason": "error"})


@bp.route('/api/server-activity/stop', methods=['POST'])
def stop_server_activity_stream():
    """Terminate an active stream with a message (Tautulli's kill move).
    Admin-only — a shared server shouldn't let anyone end others' streams."""
    try:
        if not getattr(g, 'is_admin', False):
            return jsonify({"ok": False, "error": "Admin only."}), 403
        body = request.get_json(silent=True) or {}
        from core.server_activity import stop_session
        res = stop_session(str(body.get("session_key") or ""), str(body.get("message") or ""))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception:
        logger.exception("stop stream failed")
        return jsonify({"ok": False, "error": "Failed to stop the stream."}), 500


@bp.route('/api/server-activity/image')
def get_server_activity_image():
    """Proxy a Plex image (poster/art) for the activity view so the token never
    reaches the browser. ?path=/library/metadata/.../thumb/..."""
    from flask import Response
    try:
        from core.server_activity import fetch_image
        got = fetch_image(request.args.get("path") or "")
        if not got:
            return ("", 404)
        content, ctype = got
        return Response(content, mimetype=ctype,
                        headers={"Cache-Control": "public, max-age=300"})
    except Exception:
        logger.exception("server activity image proxy failed")
        return ("", 404)
