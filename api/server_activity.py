"""server activity routes: the live view, history, stats, controls and settings.

every route is the admin's (#2155): who is watching what, on which device,
from where. an admin-minted api key passes too (the companion extension reads
it). only the artwork proxy stays open; see core/server_activity/images.py.
"""

import re

from flask import Blueprint, Response, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure() (kept for web_server's wiring; the routes import
# from core.server_activity at call time so tests can stub them)
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


_OPEN_PATHS = ('/api/server-activity/image',)
_GROUP_KEY = re.compile(r"^[A-Za-z0-9:._-]{1,160}$")


@bp.before_request
def _admin_sees_server_activity():
    if request.path in _OPEN_PATHS:
        return None
    from core.profile_context import admin_or_api_key_refusal
    return admin_or_api_key_refusal()


def _num(name, cast=float):
    v = request.args.get(name)
    if v in (None, ""):
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


@bp.route('/api/server-activity')
def get_server_activity():
    """what's playing on plex and jellyfin now, the pulse and the bandwidth
    series. never raises: no server or a down one is a state the ui shows."""
    try:
        from core.server_activity import get_activity as _live
        return jsonify(_live())
    except Exception:
        logger.exception("server activity failed")
        return jsonify({"ok": False, "reason": "error", "sessions": [], "summary": {"streams": 0}})


@bp.route('/api/server-activity/history')
def get_server_activity_history():
    """the play log as binge-grouped rows, filtered, newest first."""
    try:
        from core.server_activity import get_history
        return jsonify(get_history(
            kind=request.args.get("kind") or None, user=request.args.get("user") or None,
            method=request.args.get("method") or None, server=request.args.get("server") or None,
            start=_num("from"), end=_num("to"), days=_num("days", int), q=request.args.get("q") or None,
            page=_num("page", int) or 0, per_page=_num("per_page", int) or _num("limit", int) or 50,
            tz=request.args.get("tz") or None))
    except Exception:
        logger.exception("server activity history failed")
        return jsonify({"ok": False, "reason": "error", "groups": [], "history": []})


@bp.route('/api/server-activity/history/<group_key>')
def get_server_activity_history_group(group_key):
    """one history row opened: its plays, timeline and facts."""
    if not _GROUP_KEY.match(group_key or ""):
        return jsonify({"ok": False, "reason": "not_found"}), 404
    try:
        from core.server_activity import get_history_group
        res = get_history_group(group_key)
        return jsonify(res), (200 if res.get("ok") else 404 if res.get("reason") == "not_found" else 500)
    except Exception:
        logger.exception("server activity history group failed")
        return jsonify({"ok": False, "reason": "error"}), 500


@bp.route('/api/server-activity/stats')
def get_server_activity_stats():
    """the stats sheet over 7 / 30 / 90 / 365 days, from the play log."""
    try:
        from core.server_activity import get_stats
        return jsonify(get_stats(days=_num("days", int) or 30, tz=request.args.get("tz") or None))
    except Exception:
        logger.exception("server activity stats failed")
        return jsonify({"ok": False, "reason": "error"})


@bp.route('/api/server-activity/stop', methods=['POST'])
def stop_server_activity_stream():
    """end a stream with a message the viewer sees (plex with plex pass, jellyfin)."""
    try:
        body = request.get_json(silent=True) or {}
        from core.server_activity import stop_session
        key = str(body.get("session_key") or body.get("id") or "")
        res = stop_session(key, str(body.get("message") or ""))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception:
        logger.exception("stop stream failed")
        return jsonify({"ok": False, "error": "Failed to stop the stream."}), 500


@bp.route('/api/server-activity/message', methods=['POST'])
def message_server_activity_stream():
    """show a message on the player without stopping (jellyfin only)."""
    try:
        body = request.get_json(silent=True) or {}
        from core.server_activity import message_session
        key = str(body.get("session_key") or body.get("id") or "")
        res = message_session(key, str(body.get("message") or ""), str(body.get("header") or ""))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception:
        logger.exception("send message failed")
        return jsonify({"ok": False, "error": "Failed to send the message."}), 500


@bp.route('/api/server-activity/settings', methods=['GET', 'POST'])
def server_activity_settings():
    """upload speed, geo lookup, play log retention and stop presets."""
    from core.server_activity import get_settings, save_settings
    if request.method == 'GET':
        return jsonify({"ok": True, "settings": get_settings()})
    body = request.get_json(silent=True)
    try:
        return jsonify({"ok": True, "settings": save_settings(body if isinstance(body, dict) else None)})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception:
        logger.exception("server activity settings save failed")
        return jsonify({"ok": False, "error": "Couldn't save the settings."}), 500


@bp.route('/api/server-activity/plays/clear', methods=['POST'])
def clear_server_activity_plays():
    """empty the play log (plays still running carry on)."""
    try:
        from core.server_activity import clear_play_log
        return jsonify(clear_play_log())
    except Exception:
        logger.exception("clear play log failed")
        return jsonify({"ok": False, "error": "Couldn't clear the play log."}), 500


@bp.route('/api/server-activity/avatar')
def get_server_activity_avatar():
    """a viewer's picture, ?user=plex:<id> or jellyfin:<id>. the url comes from
    the server, never from the browser."""
    try:
        from core.server_activity import fetch_avatar
        got = fetch_avatar(request.args.get("user") or "")
        if not got:
            return ("", 404)
        content, ctype = got
        return Response(content, mimetype=ctype, headers={"Cache-Control": "private, max-age=3600"})
    except Exception:
        logger.exception("server activity avatar proxy failed")
        return ("", 404)


@bp.route('/api/server-activity/image')
def get_server_activity_image():
    """proxy a plex or jellyfin image so the token never reaches the browser.
    ?path=/library/metadata/.../thumb/... or jf:<itemId>"""
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
