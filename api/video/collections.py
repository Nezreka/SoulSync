"""Collections API (Collection Studio) — CRUD + live preview + sync for the
SoulSync-managed movie/show collections. Admin-only (gated in __init__.py).

    GET    /api/video/collections                 -> {collections:[...]}   (gallery)
    POST   /api/video/collections                 -> {ok,id}               (create)
    GET    /api/video/collections/<id>            -> {collection:{...}}     (full)
    PUT    /api/video/collections/<id>            -> {ok}                   (patch)
    DELETE /api/video/collections/<id>            -> {ok}
    POST   /api/video/collections/<id>/duplicate  -> {ok,id}
    GET    /api/video/collections/fields          -> {fields,suggestions}   (rule builder)
    GET    /api/video/collections/presets         -> {packs:[...]}          (easy setup)
    POST   /api/video/collections/presets/apply   -> {ok,created,skipped}   (batch create)
    GET    /api/video/collections/<id>/poster     -> image/jpeg             (generated art)
    POST   /api/video/collections/<id>/poster/generate -> {ok,poster_url}   (render collage)
    POST   /api/video/collections/preview         -> {ok,count,sample,...}  (live preview)
    POST   /api/video/collections/<id>/sync       -> {ok,...}               (Sync now, one)
    POST   /api/video/collections/sync            -> {ok,...}               (Sync now, all)
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.collections")

_UPDATABLE = ("name", "kind", "media_type", "definition", "poster_url", "summary",
              "sort_order", "sync_mode", "pinned", "wishlist_missing", "enabled")


def _sample_members(rows, limit=24):
    return [{"id": r.get("id"), "title": r.get("title"), "year": r.get("year"),
             "has_poster": bool(r.get("poster_url")), "tmdb_id": r.get("tmdb_id")}
            for r in rows[:limit]]


def register_routes(bp):
    @bp.route("/collections", methods=["GET"])
    def collections_list():
        from . import get_video_db
        try:
            return jsonify({"collections": get_video_db().list_collection_definitions()})
        except Exception:
            logger.exception("list collections failed")
            return jsonify({"collections": [], "error": "Failed to load collections"}), 500

    @bp.route("/collections", methods=["POST"])
    def collections_create():
        from . import get_video_db
        d = request.get_json(silent=True) or {}
        try:
            cid = get_video_db().create_collection_definition(
                d.get("name") or "Untitled collection",
                kind=d.get("kind", "smart"), media_type=d.get("media_type", "movie"),
                definition=d.get("definition"), poster_url=d.get("poster_url"),
                summary=d.get("summary"), sort_order=d.get("sort_order", "release"),
                sync_mode=d.get("sync_mode", "sync"), pinned=bool(d.get("pinned")),
                wishlist_missing=bool(d.get("wishlist_missing")),
                enabled=False if d.get("enabled") is False else True)
            if cid is None:
                return jsonify({"ok": False, "error": "Could not create collection"}), 500
            return jsonify({"ok": True, "id": cid})
        except Exception:
            logger.exception("create collection failed")
            return jsonify({"ok": False, "error": "Could not create collection"}), 500

    @bp.route("/collections/<int:cid>", methods=["GET"])
    def collections_get(cid):
        from . import get_video_db
        c = get_video_db().get_collection_definition(cid)
        if not c:
            return jsonify({"error": "not found"}), 404
        return jsonify({"collection": c})

    @bp.route("/collections/<int:cid>", methods=["PUT"])
    def collections_update(cid):
        from . import get_video_db
        d = request.get_json(silent=True) or {}
        fields = {k: d[k] for k in _UPDATABLE if k in d}
        try:
            return jsonify({"ok": get_video_db().update_collection_definition(cid, **fields)})
        except Exception:
            logger.exception("update collection %s failed", cid)
            return jsonify({"ok": False, "error": "Could not update collection"}), 500

    @bp.route("/collections/<int:cid>", methods=["DELETE"])
    def collections_delete(cid):
        from . import get_video_db
        db = get_video_db()
        # Drop our definition + ledger; the server collection is left in place
        # (the user can remove it) — we never auto-delete server objects.
        db.delete_collection_sync(cid)
        return jsonify({"ok": db.delete_collection_definition(cid)})

    @bp.route("/collections/<int:cid>/duplicate", methods=["POST"])
    def collections_duplicate(cid):
        from . import get_video_db
        nid = get_video_db().duplicate_collection_definition(cid)
        if nid is None:
            return jsonify({"ok": False, "error": "Could not duplicate"}), 500
        return jsonify({"ok": True, "id": nid})

    @bp.route("/collections/fields", methods=["GET"])
    def collections_fields():
        from . import get_video_db
        from core.video.collections.smart_filter import field_schema
        mt = request.args.get("media_type", "movie")
        mt = "show" if mt in ("show", "shows", "tv", "series") else "movie"
        genres = []
        try:
            for g in (get_video_db().top_owned_genres(mt, limit=40) or []):
                genres.append(g.get("name") if isinstance(g, dict) else g)
        except Exception:
            logger.debug("genre suggestions failed", exc_info=True)
        return jsonify({"media_type": mt, "fields": field_schema(mt),
                        "suggestions": {"genre": [g for g in genres if g]}})

    @bp.route("/collections/presets", methods=["GET"])
    def collections_presets():
        from . import get_video_db
        from core.video.collections.presets import list_packs
        mt = request.args.get("media_type", "movie")
        mt = "show" if mt in ("show", "shows", "tv", "series") else "movie"
        try:
            return jsonify({"media_type": mt, "packs": list_packs(get_video_db(), mt)})
        except Exception:
            logger.exception("preset browse failed")
            return jsonify({"media_type": mt, "packs": [], "error": "Failed to load presets"}), 500

    @bp.route("/collections/presets/apply", methods=["POST"])
    def collections_presets_apply():
        from . import get_video_db
        from core.video.collections.presets import apply_pack
        d = request.get_json(silent=True) or {}
        mt = "show" if d.get("media_type") in ("show", "shows", "tv", "series") else "movie"
        pack = str(d.get("pack") or "")
        keys = d.get("keys") or []
        if not pack or not isinstance(keys, list) or not keys:
            return jsonify({"ok": False, "error": "pack and keys are required"}), 400
        try:
            r = apply_pack(get_video_db(), pack, mt, keys,
                           wishlist_missing=bool(d.get("wishlist_missing", True)))
            _generate_posters_async([c["id"] for c in r["created"]])
            return jsonify({"ok": True, "created": r["created"], "skipped": r["skipped"]})
        except Exception:
            logger.exception("preset apply failed (%s)", pack)
            return jsonify({"ok": False, "error": "Could not create collections"}), 500

    def _generate_posters_async(ids):
        """Collage posters for freshly-applied preset collections, off-request —
        each needs member resolution + up to 4 poster fetches, so a big pack
        would otherwise hang the apply call. Best-effort: cards show art as the
        renders land (the gallery serves whatever exists at read time)."""
        if not ids:
            return
        from . import get_video_db
        db, todo = get_video_db(), list(ids)

        def run():
            try:
                from core.video.collections.poster_gen import generate_for_definitions
                generate_for_definitions(db, todo)
            except Exception:
                logger.exception("preset poster generation failed")

        import threading
        threading.Thread(target=run, name="collection-poster-gen", daemon=True).start()

    @bp.route("/collections/<int:cid>/poster", methods=["GET"])
    def collections_poster(cid):
        from flask import Response
        from core.video.collections.poster_gen import read_poster
        data = read_poster(cid)
        if not data:
            return jsonify({"error": "no generated poster"}), 404
        resp = Response(data, content_type="image/jpeg")
        # Immutable-friendly: the URL carries a content hash (?v=), so a
        # regenerate lands on a fresh URL and this can cache hard.
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp

    @bp.route("/collections/<int:cid>/poster/generate", methods=["POST"])
    def collections_poster_generate(cid):
        from . import get_video_db
        from core.video.collections.poster_gen import generate_for_definition
        db = get_video_db()
        c = db.get_collection_definition(cid)
        if not c:
            return jsonify({"ok": False, "error": "not found"}), 404
        url = generate_for_definition(db, c)
        if not url:
            return jsonify({"ok": False, "error": "Could not generate a poster"}), 500
        return jsonify({"ok": True, "poster_url": url})

    @bp.route("/collections/preview", methods=["POST"])
    def collections_preview():
        from . import get_video_db
        from core.video.collections.resolver import resolve_collection
        d = request.get_json(silent=True) or {}
        defn = {"media_type": d.get("media_type", "movie"),
                "kind": d.get("kind", "smart"),
                "definition": d.get("definition") or {}}
        # No list fetcher here: smart + franchise (owned) preview instantly from the
        # DB; a remote list source reports what's owned and notes it needs a sync.
        res = resolve_collection(get_video_db(), defn)
        if not res.ok:
            return jsonify({"ok": False, "error": res.error})
        return jsonify({"ok": True, "media_type": res.media_type,
                        "count": len(res.owned), "missing_count": len(res.missing),
                        "sample": _sample_members(res.owned)})

    @bp.route("/collections/<int:cid>/sync", methods=["POST"])
    def collections_sync_one(cid):
        from . import get_video_db
        from core.video.collections.sync import sync_one_now
        db = get_video_db()
        if not db.get_collection_definition(cid):
            return jsonify({"ok": False, "error": "not found"}), 404
        r = sync_one_now(db, cid)
        # No server configured is a client-actionable 400, not a 500.
        if not r.get("ok") and "No video server" in (r.get("error") or ""):
            return jsonify(r), 400
        return jsonify(r)

    @bp.route("/collections/sync", methods=["POST"])
    def collections_sync_all():
        from . import get_video_db
        from core.video.collections.sync import run_sync
        return jsonify(run_sync(get_video_db()))
