"""Overlay templates API (Artwork Studio).

CRUD for saved overlay designs — the visual poster-badge templates authored in
the Overlay Studio editor. This is the CREATE/EDIT side only; compositing a
template onto real poster art (the "apply" pipeline) is a separate, later module.

    GET    /api/video/overlays/templates        -> [{id,name,layer_count,...}]
    POST   /api/video/overlays/templates        -> {id}                (create)
    GET    /api/video/overlays/templates/<id>   -> {id,name,definition,...}
    PUT    /api/video/overlays/templates/<id>   -> {ok}                (patch)
    DELETE /api/video/overlays/templates/<id>   -> {ok}
    POST   /api/video/overlays/templates/<id>/duplicate -> {id}
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.overlays")


def register_routes(bp):
    @bp.route("/overlays/templates", methods=["GET"])
    def overlay_templates_list():
        from . import get_video_db
        try:
            return jsonify({"templates": get_video_db().list_overlay_templates()})
        except Exception:
            logger.exception("list overlay templates failed")
            return jsonify({"templates": [], "error": "Failed to load templates"}), 500

    @bp.route("/overlays/templates", methods=["POST"])
    def overlay_templates_create():
        from . import get_video_db
        data = request.get_json(silent=True) or {}
        name = data.get("name") or "Untitled template"
        definition = data.get("definition")
        try:
            tid = get_video_db().create_overlay_template(name, definition=definition)
            if tid is None:
                return jsonify({"ok": False, "error": "Could not create template"}), 500
            return jsonify({"ok": True, "id": tid})
        except Exception:
            logger.exception("create overlay template failed")
            return jsonify({"ok": False, "error": "Could not create template"}), 500

    @bp.route("/overlays/templates/<int:template_id>", methods=["GET"])
    def overlay_template_get(template_id):
        from . import get_video_db
        t = get_video_db().get_overlay_template(template_id)
        if not t:
            return jsonify({"error": "Not found"}), 404
        return jsonify(t)

    @bp.route("/overlays/templates/<int:template_id>", methods=["PUT", "PATCH"])
    def overlay_template_update(template_id):
        from . import get_video_db
        data = request.get_json(silent=True) or {}
        try:
            ok = get_video_db().update_overlay_template(
                template_id,
                name=data.get("name"),
                definition=data.get("definition"),
                thumbnail=data.get("thumbnail"))
            return jsonify({"ok": bool(ok)})
        except Exception:
            logger.exception("update overlay template failed for %s", template_id)
            return jsonify({"ok": False, "error": "Could not save template"}), 500

    @bp.route("/overlays/templates/<int:template_id>", methods=["DELETE"])
    def overlay_template_delete(template_id):
        from . import get_video_db
        ok = get_video_db().delete_overlay_template(template_id)
        return jsonify({"ok": bool(ok)})

    @bp.route("/overlays/templates/<int:template_id>/duplicate", methods=["POST"])
    def overlay_template_duplicate(template_id):
        from . import get_video_db
        tid = get_video_db().duplicate_overlay_template(template_id)
        if tid is None:
            return jsonify({"ok": False, "error": "Could not duplicate"}), 404
        return jsonify({"ok": True, "id": tid})

    # ── apply: assignment + run ───────────────────────────────────────────────
    @bp.route("/overlays/assignments", methods=["GET"])
    def overlay_assignments_get():
        from . import get_video_db
        db = get_video_db()
        templates = [{"id": t["id"], "name": t["name"]} for t in db.list_overlay_templates()]
        return jsonify({"assignments": db.get_overlay_assignments(), "templates": templates,
                        "applied": db.overlay_applied_count()})

    @bp.route("/overlays/assignments", methods=["PUT"])
    def overlay_assignments_set():
        from . import get_video_db
        data = request.get_json(silent=True) or {}
        scope = data.get("scope")
        ok = get_video_db().set_overlay_assignment(scope, data.get("template_id"), bool(data.get("enabled")))
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)

    @bp.route("/overlays/apply", methods=["POST"])
    def overlay_apply_run():
        from . import get_video_db
        from core.video.overlays import service
        data = request.get_json(silent=True) or {}
        scope = data.get("scope") or "both"
        scopes = ["movie", "show"] if scope == "both" else [scope]
        scopes = [s for s in scopes if s in ("movie", "show")]
        if not scopes:
            return jsonify({"ok": False, "error": "bad scope"}), 400
        started = service.start(get_video_db(), scopes, force=bool(data.get("force")),
                                remove=bool(data.get("remove")))
        if not started:
            return jsonify({"ok": False, "error": "A run is already in progress"}), 409
        return jsonify({"ok": True, "started": True})

    @bp.route("/overlays/apply/status", methods=["GET"])
    def overlay_apply_status():
        from core.video.overlays import service
        return jsonify(service.status())

    @bp.route("/overlays/sample/<kind>/<int:item_id>", methods=["GET"])
    def overlay_sample(kind, item_id):
        """Real badge values for a library item — the editor's "load from a real
        title" so dynamic badges preview against actual data."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "kind must be 'movie' or 'show'"}), 400
        data = get_video_db().overlay_sample_data(kind, item_id)
        if data is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"sample": data})
