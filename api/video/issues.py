"""Video Issues API — the music /api/issues contract, video-scoped.

Same lifecycle (open → in_progress → resolved | dismissed, reopenable, resolved
retained), same permission split (any profile reports and sees/withdraws its
OWN issues; the admin sees all, gets the reporter name, and resolves), same
snapshot pattern (the item's state denormalized at report time so the report
outlives library changes). Caller identity rides flask.g (set app-wide by
web_server's before_request) — never the request body.
"""

from __future__ import annotations

from flask import g, jsonify, request

from core.issues import service as issue_service
from core.issues.activity import fix_action
from core.issues.lifecycle import strip_reporter, visible_to

from utils.logging_config import get_logger

logger = get_logger("video_api.issues")

ENTITY_TYPES = ("movie", "show", "episode")

# category -> (label, which entity types it applies to). The video sibling of
# the music 10-category enum; the frontend mirrors this in video-issues.js.
CATEGORIES = {
    "wrong_match": ("Matched to the wrong title", ("movie", "show")),
    "wrong_metadata": ("Wrong details (year, summary, genres…)", ("movie", "show", "episode")),
    "wrong_poster": ("Wrong poster / artwork", ("movie", "show")),
    "bad_quality": ("Poor video quality", ("movie", "episode")),
    "audio_issue": ("Audio problem (sync, language, missing)", ("movie", "episode")),
    "subtitle_issue": ("Subtitle problem", ("movie", "episode")),
    "playback_issue": ("Won't play / buffers / stutters", ("movie", "episode")),
    "missing_content": ("Missing episodes or seasons", ("show",)),
    "duplicate": ("Duplicate copies", ("movie", "show")),
    "other": ("Something else", ("movie", "show", "episode")),
}


def _is_admin() -> bool:
    # The REAL admin flag (web_server stashes g.is_admin from the profile —
    # music supports secondary admins, and the frontend checks the same flag).
    # Fallback: the video convention, profile 1.
    return bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))


def _pid() -> int:
    return int(getattr(g, "profile_id", 1) or 1)


def _notify(profile_id, message, kind="info"):
    from core.profile_notify import notify_profile
    notify_profile(profile_id, message, kind, link="video-issues")


def _side():
    from . import get_video_db
    db = get_video_db()

    def create(pid, entity_type, entity_id, category, title, description, snapshot, priority, name):
        return db.create_issue(pid, entity_type, entity_id, category, title, description=description,
                               snapshot_data=snapshot, priority=priority, reporter_name=name or None)

    return issue_service.IssueSide(get=db.get_issue, create=create, update=db.update_issue,
                                   store=db.issue_threads, notify=_notify, categories=tuple(CATEGORIES),
                                   strip_reporter_for_members=True, side="video")


def _snapshot(db, entity_type: str, entity_id) -> dict:
    """The item's state at report time — enough for the admin to see exactly
    what was reported without re-opening the item (and even if it changes)."""
    try:
        if entity_type == "movie":
            d = db.movie_detail(int(entity_id)) or {}
            return {k: d.get(k) for k in
                    ("title", "year", "tmdb_id", "imdb_id", "content_rating", "runtime_minutes",
                     "genres", "overview", "has_poster", "owned", "files")} | {
                    "poster": f"/api/video/poster/movie/{entity_id}" if d.get("has_poster") else None}
        if entity_type == "show":
            d = db.show_detail(int(entity_id)) or {}
            return {k: d.get(k) for k in
                    ("title", "year", "tmdb_id", "tvdb_id", "imdb_id", "status", "network",
                     "genres", "overview", "has_poster", "season_count",
                     "episode_total", "episode_owned")} | {
                    "poster": f"/api/video/poster/show/{entity_id}" if d.get("has_poster") else None}
        if entity_type == "episode":
            row = db.poster_set_target("episode", int(entity_id))
            snap = {"episode_row": dict(row) if row else None}
            conn = db._get_connection()
            try:
                e = conn.execute(
                    "SELECT e.season_number, e.episode_number, e.title, e.air_date, "
                    "s.id AS show_id, s.title AS show_title, s.tmdb_id "
                    "FROM episodes e JOIN shows s ON s.id=e.show_id WHERE e.id=?",
                    (int(entity_id),)).fetchone()
            finally:
                conn.close()
            if e:
                snap.update(dict(e))
                snap["poster"] = f"/api/video/poster/show/{e['show_id']}"
                snap["code"] = "S%02dE%02d" % (e["season_number"] or 0, e["episode_number"] or 0)
            return snap
    except Exception as ex:   # noqa: BLE001 - a snapshot failure never blocks the report
        logger.exception("issue snapshot failed for %s %s", entity_type, entity_id)
        return {"_snapshot_error": str(ex)}
    return {}


def register_routes(bp):
    @bp.route("/issues", methods=["GET"])
    def video_issues_list():
        from . import get_video_db
        q = request.args
        issues = get_video_db().get_issues(
            _pid(), status=q.get("status") or None, category=q.get("category") or None,
            entity_type=q.get("entity_type") or None,
            limit=q.get("limit", 100), offset=q.get("offset", 0), is_admin=_is_admin())
        for i in issues:
            i["fix_action"] = fix_action(i.get("category") or "")
            if not _is_admin():   # reporter identity is admin-only (music standard)
                i.pop("reporter_name", None)
        counts = get_video_db().get_issue_counts(_is_admin(), _pid())
        if q.get("status") and q.get("status") != "all":
            total = counts.get(q.get("status"), len(issues))
        else:
            total = counts.get("total", len(issues))
        return jsonify({"success": True, "issues": issues, "total": total})

    @bp.route("/issues", methods=["POST"])
    def video_issues_create():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        entity_type = body.get("entity_type")
        entity_id = body.get("entity_id")
        category = body.get("category")
        if entity_type not in ENTITY_TYPES:
            return jsonify({"success": False, "error": "bad entity_type"}), 400
        if category not in CATEGORIES or entity_type not in CATEGORIES[category][1]:
            return jsonify({"success": False, "error": "bad category"}), 400
        if entity_id in (None, ""):
            return jsonify({"success": False, "error": "title and entity_id required"}), 400
        db = get_video_db()
        payload, code = issue_service.report(
            _side(), actor=_pid(), actor_name=getattr(g, "profile_name", None) or "",
            is_admin=_is_admin(), entity_type=entity_type, entity_id=str(entity_id),
            category=category, body=body, snapshot=lambda: _snapshot(db, entity_type, entity_id))
        if code == 400:
            payload["error"] = "title and entity_id required"
        return jsonify(payload), code

    @bp.route("/issues/<int:issue_id>", methods=["GET"])
    def video_issues_get(issue_id):
        from . import get_video_db
        payload, code = issue_service.detail(_side(), actor=_pid(), is_admin=_is_admin(),
                                             issue_id=issue_id)
        if code == 404:
            payload["error"] = "not found"
        return jsonify(payload), code

    @bp.route("/issues/<int:issue_id>", methods=["PUT"])
    def video_issues_update(issue_id):
        from . import get_video_db
        db = get_video_db()
        issue = db.get_issue(issue_id)
        if not issue:
            return jsonify({"success": False, "error": "not found"}), 404
        body = request.get_json(silent=True) or {}
        # Owners may edit only their own issue's title/description (music
        # rule) — ANY other field in the payload is an outright 403, never
        # a silent partial apply. someone else's issue stays 403 here (the
        # contract the video tests pin), GET hides it as 404.
        if not _is_admin() and issue["profile_id"] != _pid():
            return jsonify({"success": False, "error": "forbidden"}), 403
        payload, code = issue_service.triage(_side(), actor=_pid(),
                                             actor_name=getattr(g, "profile_name", None) or "",
                                             is_admin=_is_admin(), issue_id=issue_id, body=body)
        if code == 403:
            payload["error"] = "forbidden"
        return jsonify(payload), code

    @bp.route("/issues/<int:issue_id>/comments", methods=["POST"])
    def video_issues_comment(issue_id):
        """reply on an issue: its reporter, a follower, or an admin."""
        payload, code = issue_service.comment(_side(), actor=_pid(),
                                              actor_name=getattr(g, "profile_name", None) or "",
                                              is_admin=_is_admin(), issue_id=issue_id,
                                              body=request.get_json(silent=True) or {})
        return jsonify(payload), code

    @bp.route("/issues/<int:issue_id>", methods=["DELETE"])
    def video_issues_delete(issue_id):
        from . import get_video_db
        db = get_video_db()
        issue = db.get_issue(issue_id)
        if not issue:
            return jsonify({"success": False, "error": "not found"}), 404
        if not _is_admin() and issue["profile_id"] != _pid():
            return jsonify({"success": False, "error": "forbidden"}), 403
        return jsonify({"success": db.delete_issue(issue_id)})

    @bp.route("/issues/counts", methods=["GET"])
    def video_issues_counts():
        from . import get_video_db
        db = get_video_db()
        counts = db.get_issue_counts(_is_admin(), _pid())
        counts["updates"] = 0 if _is_admin() else db.issue_threads.unread_count(_pid())
        return jsonify({"success": True, "counts": counts})

    @bp.route("/issues/categories", methods=["GET"])
    def video_issues_categories():
        """The category enum for the report form (label + applicable types)."""
        return jsonify({"categories": [
            {"key": k, "label": v[0], "applies": list(v[1])} for k, v in CATEGORIES.items()]})