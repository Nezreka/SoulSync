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


def _prerender_thumb(db, template_id):
    """Fire a background render+cache of a template's gallery thumbnail (best-effort)."""
    try:
        from core.video.overlays.service import prerender_thumb_async
        prerender_thumb_async(db, template_id)
    except Exception:
        logger.warning("prerender thumb kickoff failed for %s", template_id, exc_info=True)


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
            db = get_video_db()
            tid = db.create_overlay_template(name, definition=definition)
            if tid is None:
                return jsonify({"ok": False, "error": "Could not create template"}), 500
            _prerender_thumb(db, tid)
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
            db = get_video_db()
            ok = db.update_overlay_template(
                template_id,
                name=data.get("name"),
                definition=data.get("definition"),
                thumbnail=data.get("thumbnail"))
            if ok and data.get("definition") is not None:   # re-render the cached thumb off-thread
                _prerender_thumb(db, template_id)
            return jsonify({"ok": bool(ok)})
        except Exception:
            logger.exception("update overlay template failed for %s", template_id)
            return jsonify({"ok": False, "error": "Could not save template"}), 500

    @bp.route("/overlays/templates/<int:template_id>", methods=["DELETE"])
    def overlay_template_delete(template_id):
        from . import get_video_db
        ok = get_video_db().delete_overlay_template(template_id)
        if ok:
            try:
                from core.video.overlays.assets import AssetStore
                AssetStore.default().clear_thumb(template_id)
            except Exception:
                logger.warning("clear cached thumb failed for %s", template_id, exc_info=True)
        return jsonify({"ok": bool(ok)})

    @bp.route("/overlays/templates/<int:template_id>/thumb", methods=["GET"])
    def overlay_template_thumb(template_id):
        """A rendered mini-preview of the template (compositor onto a neutral poster)
        for the gallery cards."""
        from flask import Response, abort
        from . import get_video_db
        from core.video.overlays.compositor import render_template_thumbnail
        db = get_video_db()
        t = db.get_overlay_template(template_id)
        if not t:
            abort(404)
        definition = t.get("definition") or {}
        data = None
        try:                                # cached (from save-time pre-render) → render on miss
            from core.video.overlays.service import get_or_render_thumb
            data = get_or_render_thumb(db, template_id, definition)
        except Exception:
            logger.warning("overlay thumbnail cache path failed for %s", template_id, exc_info=True)
        if data is None:                    # last-resort neutral render (never cached)
            try:
                data = render_template_thumbnail(definition)
            except Exception:
                logger.exception("overlay thumbnail failed for %s", template_id)
                abort(404)
        resp = Response(data, content_type="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    @bp.route("/overlays/templates/<int:template_id>/duplicate", methods=["POST"])
    def overlay_template_duplicate(template_id):
        from . import get_video_db
        db = get_video_db()
        tid = db.duplicate_overlay_template(template_id)
        if tid is None:
            return jsonify({"ok": False, "error": "Could not duplicate"}), 404
        _prerender_thumb(db, tid)
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
        req_scopes = data.get("scopes")
        if isinstance(req_scopes, list) and req_scopes:
            scopes = req_scopes                       # explicit set (movie/show + opted-in sub scopes)
        else:
            scopes = ["movie", "show"] if scope == "both" else [scope]
        seen = set()
        scopes = [s for s in scopes if s in ("movie", "show", "season", "episode")
                  and not (s in seen or seen.add(s))]
        if not scopes:
            return jsonify({"ok": False, "error": "bad scope"}), 400
        started = service.start(get_video_db(), scopes, force=bool(data.get("force")),
                                remove=bool(data.get("remove")), reset=bool(data.get("reset")))
        if not started:
            return jsonify({"ok": False, "error": "A run is already in progress"}), 409
        return jsonify({"ok": True, "started": True})

    @bp.route("/overlays/apply/status", methods=["GET"])
    def overlay_apply_status():
        from core.video.overlays import service
        return jsonify(service.status())

    # ── uploaded template images ──────────────────────────────────────────────
    @bp.route("/overlays/upload", methods=["POST"])
    def overlay_upload():
        """Store an uploaded image for use as an Image layer. Returns an
        ``asset://<name>`` ref (rendered/served same-origin)."""
        from core.video.overlays.assets import AssetStore
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "No file"}), 400
        data = f.read()
        if not data or len(data) > 8 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Empty or over 8 MB"}), 400
        ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "png")
        try:
            name = AssetStore.default().save_upload(data, ext)
        except Exception:
            logger.exception("overlay upload failed")
            return jsonify({"ok": False, "error": "Could not save"}), 500
        return jsonify({"ok": True, "src": "asset://" + name})

    @bp.route("/overlays/asset/<name>", methods=["GET"])
    def overlay_asset(name):
        from flask import Response, abort
        from core.video.overlays.assets import AssetStore
        data = AssetStore.default().read_upload(name)
        if data is None:
            abort(404)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
        ctype = "image/jpeg" if ext in ("jpg", "jpeg") else ("image/" + ext)
        resp = Response(data, content_type=ctype)
        resp.headers["Cache-Control"] = "public, max-age=31536000"
        return resp

    @bp.route("/overlays/logo/<field>/<path:value>", methods=["GET"])
    def overlay_logo(field, value):
        """Serve the drop-in-pack logo for a field value, so the editor previews
        the real mark. 404 when no pack/match — the editor falls back to text."""
        from flask import Response, abort
        from core.video.overlays.assets import AssetStore
        from core.video.overlays.logos import logo_ref
        ref = logo_ref(field, value)
        if not ref:
            abort(404)
        data = AssetStore.default().read_logo(ref[0], ref[1])
        if data is None:
            abort(404)
        head = data[:12]
        ctype = "image/png" if head.startswith(b"\x89PNG") else \
            ("image/webp" if head[8:12] == b"WEBP" else "image/jpeg")
        resp = Response(data, content_type=ctype)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @bp.route("/overlays/logopack", methods=["GET"])
    def overlay_logopack_status():
        """What logo art is installed — powers the palette gate (grey out the Logo
        badge until a pack exists) and the per-field status in the install popup."""
        from core.video.overlays import logo_packs
        from core.video.overlays.assets import AssetStore
        st = logo_packs.pack_status(AssetStore.default())
        st["job"] = logo_packs.install_status()
        return jsonify(st)

    @bp.route("/overlays/logopack/install", methods=["POST"])
    def overlay_logopack_install():
        """Copy Kometa's public logo set into the local drop-in folders (opt-in;
        user-initiated). Returns immediately — poll /overlays/logopack for progress."""
        from core.video.overlays import logo_packs
        from core.video.overlays.assets import AssetStore
        started = logo_packs.start_install(AssetStore.default())
        return jsonify({"ok": True, "started": started, "job": logo_packs.install_status()})

    @bp.route("/overlays/preview/random", methods=["GET"])
    def overlay_preview_random():
        """A random owned item to drop into the editor's preview — the "surprise me"
        companion to the search. ?kind=poster|season|episode matches the template."""
        from . import get_video_db
        kind = request.args.get("kind") or "poster"
        return jsonify({"item": get_video_db().random_overlay_preview_item(kind)})

    @bp.route("/overlays/preview/search", methods=["GET"])
    def overlay_preview_search():
        """Search seasons/episodes to preview a Season/Episode template on a real
        one. (Poster templates use the general /library search.)"""
        from . import get_video_db
        kind = request.args.get("kind") or "season"
        q = request.args.get("q") or request.args.get("search") or ""
        return jsonify({"items": get_video_db().search_overlay_preview(kind, q)})

    @bp.route("/overlays/preview/filmstrip", methods=["POST"])
    def overlay_preview_filmstrip():
        """Render the (unsaved) template onto N random real titles so the editor can
        show it holds across varying data. Body: {definition, n}."""
        from . import get_video_db
        from core.video.overlays import service
        data = request.get_json(silent=True) or {}
        n = max(1, min(6, int(data.get("n") or 4)))
        try:
            frames = service.preview_filmstrip(get_video_db(), data.get("definition") or {}, n)
        except Exception:
            logger.exception("overlay filmstrip render failed")
            return jsonify({"ok": False, "frames": [], "error": "Render failed"}), 500
        return jsonify({"ok": True, "frames": frames})

    @bp.route("/overlays/sample/<kind>/<int:item_id>", methods=["GET"])
    def overlay_sample(kind, item_id):
        """Real badge values for a library item — the editor's "load from a real
        title" so dynamic badges preview against actual data."""
        from . import get_video_db
        if kind not in ("movie", "show", "season", "episode"):
            return jsonify({"error": "kind must be movie, show, season, or episode"}), 400
        data = get_video_db().overlay_sample_data(kind, item_id)
        if data is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"sample": data})
