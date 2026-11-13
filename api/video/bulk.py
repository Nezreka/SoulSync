"""Bulk metadata operations (the library grid's multi-select action bar).

POST /api/video/bulk/start   {kind, ids[], action, params} → background job
                             (action 'collection_add' runs inline — one write)
GET  /api/video/bulk/status  → job state (polling fallback for the bell)

Applies each item through core.video.metadata — the same edit-and-lock engine
as the Manage sidebar, so bulk edits push to the server and survive scans too.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.bulk")


def register_routes(bp):
    @bp.route("/bulk/start", methods=["POST"])
    def video_bulk_start():
        from core.video import bulk_ops

        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        ids = body.get("ids")
        action = body.get("action")
        params = body.get("params") or {}
        db = get_video_db()
        if action == "collection_add":
            if kind not in ("movie", "show") or not isinstance(ids, list) or not ids:
                return jsonify({"ok": False, "error": "bad request"}), 400
            try:
                cid = int(params.get("collection_id"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "collection_id required"}), 400
            res = bulk_ops.add_to_collection(db, kind, ids, cid)
            return jsonify(res), (200 if res.get("ok") else 400)
        # who asked: a wish lands on their wishlist, a delete is theirs
        try:
            from flask import g
            params = dict(params, profile_id_of_user=int(getattr(g, "profile_id", 1) or 1))
        except Exception:   # noqa: BLE001 - outside a profile: the admin
            params = dict(params, profile_id_of_user=1)
        if action == "quality_profile" and params.get("profile_id"):
            named = db.get_named_quality_profile(params["profile_id"]) if isinstance(
                params["profile_id"], int) else None
            if not named:
                return jsonify({"ok": False, "error": "That quality profile is gone."}), 400
            params["profile_name"] = named.get("name")
        res = bulk_ops.start_bulk(db, kind, ids, action, params)
        if not res.get("ok"):
            busy = res.get("error") == "a bulk operation is already running"
            return jsonify(res), (409 if busy else 400)
        return jsonify(res)

    @bp.route("/bulk/status", methods=["GET"])
    def video_bulk_status():
        from core.video.bulk_ops import bulk_status
        return jsonify(bulk_status())
