"""Video watchlist API — the user's curated follow-list of shows + people.

Mirrors the music watchlist's add/remove/list/check shape. v1 just manages
membership (so cards can toggle + the Watchlist page can render); the
monitoring/discovery engine that turns a follow into new downloads is a later
phase. Reads/writes only video_library.db via the shared VideoDatabase.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.watchlist")

_KINDS = ("show", "person")


def register_routes(bp):
    @bp.route("/watchlist", methods=["GET"])
    def video_watchlist_list():
        """All watchlist entries grouped by kind (for the tabbed page)."""
        from . import get_video_db
        try:
            db = get_video_db()
            kind = request.args.get("kind")
            if kind in _KINDS:
                items = db.list_watchlist(kind)
                return jsonify({"success": True, "kind": kind, "items": items})
            rows = db.list_watchlist()
            shows = [r for r in rows if r.get("kind") == "show"]
            people = [r for r in rows if r.get("kind") == "person"]
            return jsonify({"success": True, "shows": shows, "people": people,
                            "counts": {"show": len(shows), "person": len(people),
                                       "total": len(rows)}})
        except Exception:
            logger.exception("Failed to list video watchlist")
            return jsonify({"success": False, "error": "Failed to load watchlist"}), 500

    @bp.route("/watchlist/counts", methods=["GET"])
    def video_watchlist_counts():
        from . import get_video_db
        try:
            return jsonify({"success": True, **get_video_db().watchlist_counts()})
        except Exception:
            logger.exception("Failed to count video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/add", methods=["POST"])
    def video_watchlist_add():
        """Add a show/person. Body: {kind, tmdb_id, title, poster_url?, library_id?}."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        title = (body.get("title") or "").strip()
        if kind not in _KINDS or not tmdb_id or not title:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        try:
            ok = get_video_db().add_to_watchlist(
                kind, int(tmdb_id), title,
                poster_url=body.get("poster_url") or None,
                library_id=body.get("library_id") or None)
            if not ok:
                return jsonify({"success": False, "error": "Could not add to watchlist"}), 400
            return jsonify({"success": True, "watched": True})
        except Exception:
            logger.exception("Failed to add to video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/remove", methods=["POST"])
    def video_watchlist_remove():
        """Remove a show/person. Body: {kind, tmdb_id}."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        if kind not in _KINDS or not tmdb_id:
            return jsonify({"success": False, "error": "kind and tmdb_id are required"}), 400
        try:
            removed = get_video_db().remove_from_watchlist(kind, int(tmdb_id))
            return jsonify({"success": True, "watched": False, "removed": removed})
        except Exception:
            logger.exception("Failed to remove from video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/check", methods=["POST"])
    def video_watchlist_check():
        """Hydrate cards. Body: {kind, tmdb_ids: [...]} → {results: {id: true}}.
        Only watched ids appear in results (absent = not watched)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        ids = body.get("tmdb_ids") or []
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind is required"}), 400
        try:
            state = get_video_db().watchlist_state(kind, ids)
            # JSON object keys must be strings.
            return jsonify({"success": True, "results": {str(k): True for k in state}})
        except Exception:
            logger.exception("Failed to check video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500
