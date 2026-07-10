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
    POST   /api/video/collections/<id>/poster/generate -> {ok,poster_url}   (render one, mode auto|collage)
    POST   /api/video/collections/posters/regenerate -> {ok,total}          (refresh ALL owned art)
    GET    /api/video/collections/server          -> {collections:[...]}    (all ON the server)
    POST   /api/video/collections/server/delete   -> {ok,total}             (start bulk cleanup job)
    GET    /api/video/collections/server/delete/status -> job state         (polling fallback)
    POST   /api/video/collections/server/adopt    -> {ok,adopted,skipped}   (manage existing collections)
    POST   /api/video/collections/preview         -> {ok,count,sample,...}  (live preview)
    POST   /api/video/collections/<id>/sync       -> {ok,...}               (Sync now, one)
    POST   /api/video/collections/sync            -> {ok,started}           (start sync-all job)
    GET    /api/video/collections/sync/status     -> job state              (bell seed / polling)
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.collections")

_UPDATABLE = ("name", "kind", "media_type", "definition", "poster_url", "summary",
              "sort_order", "sync_mode", "pinned", "wishlist_missing", "enabled",
              "window_start", "window_end")


def _clean_md(v):
    """A seasonal-window value: valid 'MM-DD' passes, '' clears, junk → ''."""
    import re
    v = (str(v).strip() if v is not None else "")
    return v if re.match(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$", v) else ""


def _sample_members(rows, limit=24):
    return [{"id": r.get("id"), "title": r.get("title"), "year": r.get("year"),
             "has_poster": bool(r.get("poster_url")), "tmdb_id": r.get("tmdb_id")}
            for r in rows[:limit]]


def register_routes(bp):
    @bp.route("/collections", methods=["GET"])
    def collections_list():
        from . import get_video_db
        from core.video.collections.sync import in_season
        try:
            rows = get_video_db().list_collection_definitions()
            for r in rows:
                # Only meaningful when a window is set; None = not seasonal.
                r["in_season"] = in_season(r) if (r.get("window_start") and r.get("window_end")) else None
            return jsonify({"collections": rows})
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
                enabled=False if d.get("enabled") is False else True,
                window_start=_clean_md(d.get("window_start")),
                window_end=_clean_md(d.get("window_end")))
            if cid is None:
                return jsonify({"ok": False, "error": "Could not create collection"}), 500
            # Art is default-on: a poster-less collection gets its collage
            # rendered off-request (regenerate any time from the editor).
            if not d.get("poster_url"):
                _generate_posters_async([cid])
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
        for w in ("window_start", "window_end"):
            if w in fields:
                fields[w] = _clean_md(fields[w])
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

    @bp.route("/collections/presets/catalog", methods=["GET"])
    def collections_presets_catalog():
        """Pack identities only (no DB, no network) — the browser paints these
        instantly as skeletons while the full expansion loads."""
        from core.video.collections.presets import pack_catalog
        mt = request.args.get("media_type", "movie")
        mt = "show" if mt in ("show", "shows", "tv", "series") else "movie"
        return jsonify({"media_type": mt, "packs": pack_catalog(mt)})

    # Full-expansion cache: on a big library the aggregate queries take seconds,
    # and the counts barely move between clicks — serve the cached payload for
    # 10 min with FRESH exists-marking (that's the part that changes on apply).
    _preset_cache: dict = {}
    _PRESET_TTL = 600

    def _remark_exists(db, mt, packs):
        try:
            existing = {" ".join((c.get("name") or "").split()).casefold()
                        for c in db.list_collection_definitions() or []
                        if (c.get("media_type") or "movie") == mt}
        except Exception:
            existing = set()
        for p in packs:
            for e in p.get("entries") or []:
                e["exists"] = " ".join((e.get("name") or "").split()).casefold() in existing

    @bp.route("/collections/presets", methods=["GET"])
    def collections_presets():
        import time as _time
        from . import get_video_db
        from core.video.collections.list_sources import build_list_fetcher
        from core.video.collections.presets import list_packs
        mt = request.args.get("media_type", "movie")
        mt = "show" if mt in ("show", "shows", "tv", "series") else "movie"
        try:
            db = get_video_db()
            hit = _preset_cache.get(mt)
            if hit and (_time.monotonic() - hit["ts"]) < _PRESET_TTL:
                _remark_exists(db, mt, hit["packs"])
                return jsonify({"media_type": mt, "packs": hit["packs"]})
            if mt == "movie":
                # Drain the franchise-id backlog off-request so the Franchises
                # pack stops under-reporting (the lazy 20-per-Discover-visit
                # backfill starves it on older libraries).
                from core.video.collections.presets import kick_franchise_backfill
                kick_franchise_backfill(db)
            # The fetcher powers the remote packs' live "owned / chart size"
            # counts (engine-cached; a failed fetch degrades to count=None).
            packs = list_packs(db, mt, fetcher=build_list_fetcher(db))
            _preset_cache[mt] = {"ts": _time.monotonic(), "packs": packs}
            return jsonify({"media_type": mt, "packs": packs})
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
            from core.video.collections.list_sources import build_list_fetcher
            db = get_video_db()
            r = apply_pack(db, pack, mt, keys,
                           wishlist_missing=bool(d.get("wishlist_missing", True)),
                           fetcher=build_list_fetcher(db))
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

    @bp.route("/collections/posters/regenerate", methods=["POST"])
    def collections_posters_regenerate():
        """Re-render EVERY collection's artwork with the current pipeline
        (context art first) in the background — only touches generated posters
        and poster-less collections; hand-set poster URLs are never clobbered.
        The next sync pushes the refreshed art (the ?v hash changes)."""
        from . import get_video_db
        from core.video.collections.poster_gen import kick_regenerate_all
        r = kick_regenerate_all(get_video_db())
        if not r.get("ok"):
            return jsonify(r), 409
        return jsonify(r)

    @bp.route("/collections/posters/regenerate/status", methods=["GET"])
    def collections_posters_regenerate_status():
        from core.video.collections.poster_gen import artwork_status
        return jsonify(artwork_status())

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
        """Render this collection's artwork. mode 'auto' (default) uses the
        subject's real TMDB art when it has one (franchise/universe title art,
        a director's portrait); 'collage' forces the member-poster collage."""
        from . import get_video_db
        from core.video.collections.poster_gen import generate_for_definition
        db = get_video_db()
        c = db.get_collection_definition(cid)
        if not c:
            return jsonify({"ok": False, "error": "not found"}), 404
        d = request.get_json(silent=True) or {}
        mode = "collage" if d.get("mode") == "collage" else "auto"
        url = generate_for_definition(db, c, mode=mode)
        if not url:
            return jsonify({"ok": False, "error": "Could not generate a poster"}), 500
        return jsonify({"ok": True, "poster_url": url})

    @bp.route("/collections/search_owned", methods=["GET"])
    def collections_search_owned():
        """Owned-title search for the include-override picker."""
        from . import get_video_db
        mt = "show" if request.args.get("media_type") in ("show", "shows", "tv") else "movie"
        q = request.args.get("q", "")
        return jsonify({"results": get_video_db().search_owned_titles(mt, q, limit=10)})

    @bp.route("/collections/titles", methods=["GET"])
    def collections_titles():
        """Titles for override chips: ?media_type=&tmdb_ids=1,2,3."""
        from . import get_video_db
        mt = "show" if request.args.get("media_type") in ("show", "shows", "tv") else "movie"
        try:
            ids = [int(x) for x in (request.args.get("tmdb_ids") or "").split(",") if x.strip()]
        except ValueError:
            ids = []
        return jsonify({"titles": get_video_db().owned_titles_by_tmdb_ids(mt, ids)})

    @bp.route("/collections/preview", methods=["POST"])
    def collections_preview():
        from . import get_video_db
        from core.video.collections.resolver import resolve_collection
        d = request.get_json(silent=True) or {}
        defn = {"media_type": d.get("media_type", "movie"),
                "kind": d.get("kind", "smart"),
                "definition": d.get("definition") or {}}
        # Smart + franchise preview straight from the DB; remote sources (charts/
        # keywords/lists) go through the real fetcher — engine-cached, so the
        # debounced editor preview stays snappy after the first resolve.
        from core.video.collections.list_sources import build_list_fetcher
        db = get_video_db()
        res = resolve_collection(db, defn, list_fetcher=build_list_fetcher(db))
        if not res.ok:
            return jsonify({"ok": False, "error": res.error})
        return jsonify({"ok": True, "media_type": res.media_type,
                        "count": len(res.owned), "missing_count": len(res.missing),
                        "sample": _sample_members(res.owned)})

    @bp.route("/collections/<int:cid>/missing", methods=["GET"])
    def collections_missing(cid):
        """The titles a list collection's source has that the library doesn't —
        the acquisition view behind the preview's '+N missing' badge."""
        from . import get_video_db
        from core.video.collections.list_sources import build_list_fetcher
        from core.video.collections.resolver import resolve_collection
        db = get_video_db()
        c = db.get_collection_definition(cid)
        if not c:
            return jsonify({"ok": False, "error": "not found"}), 404
        res = resolve_collection(db, c, list_fetcher=build_list_fetcher(db))
        if not res.ok:
            return jsonify({"ok": False, "error": res.error})
        return jsonify({"ok": True, "count": len(res.missing), "missing": res.missing})

    @bp.route("/collections/<int:cid>/wishlist_missing", methods=["POST"])
    def collections_wishlist_missing(cid):
        """Wishlist a collection's missing members ON DEMAND (the browser's
        'wishlist all' / per-title buttons) — an explicit user action, so it
        works regardless of the collection's nightly wishlist toggle. Optional
        {tmdb_ids: [...]} restricts to a subset."""
        from . import get_video_db
        from core.video.collections.list_sources import build_list_fetcher
        from core.video.collections.resolver import resolve_collection
        from core.video.collections.sync import wishlist_missing_members
        db = get_video_db()
        c = db.get_collection_definition(cid)
        if not c:
            return jsonify({"ok": False, "error": "not found"}), 404
        res = resolve_collection(db, c, list_fetcher=build_list_fetcher(db))
        if not res.ok:
            return jsonify({"ok": False, "error": res.error})
        missing = res.missing
        d = request.get_json(silent=True) or {}
        if d.get("tmdb_ids"):
            want = {int(x) for x in d["tmdb_ids"] if str(x).strip().isdigit()}
            missing = [m for m in missing if int(m["tmdb_id"]) in want]
        # Explicit action: force the wishlist gate open for this run only.
        added = wishlist_missing_members(db, dict(c, wishlist_missing=True), missing)
        return jsonify({"ok": True, "added": added,
                        "unit": "episodes" if (c.get("media_type") == "show") else "movies"})

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
        """START the full sync in the background (charts fetch + default-on
        poster generation make it long-running). Progress streams over the
        'collections:sync' socket event (the bell + the studio button);
        GET .../sync/status is the polling fallback."""
        from . import get_video_db
        from core.video.collections.sync_job import start_sync_all
        r = start_sync_all(get_video_db())
        if not r.get("ok"):
            return jsonify(r), 409
        return jsonify(r)

    @bp.route("/collections/sync/status", methods=["GET"])
    def collections_sync_status():
        from core.video.collections.sync_job import status
        return jsonify(status())

    # ── server-side collections (cleanup view) ────────────────────────────────
    @bp.route("/collections/server", methods=["GET"])
    def collections_on_server():
        """Everything that exists ON the media server right now — SoulSync-managed
        AND foreign (old Kometa runs, hand-made). Managed ones are marked via the
        sync ledger so the cleanup view can target just the foreign leftovers."""
        from . import get_video_db
        from core.video.collections.sync import get_collection_source
        src = get_collection_source()
        if src is None or not hasattr(src, "list_collections"):
            return jsonify({"ok": False, "error": "No video server configured (or it can't do collections)"}), 400
        try:
            cols = src.list_collections()
        except Exception:
            logger.exception("list server collections failed")
            return jsonify({"ok": False, "error": "Could not read collections from the server"}), 502
        managed = {}
        for s in get_video_db().list_collection_syncs():
            if s.get("server_source") == src.server_name and s.get("server_id"):
                managed[str(s["server_id"])] = s
        kometa_labels = {"kometa", "pmm", "plex meta manager"}
        for c in cols:
            m = managed.get(str(c.get("server_id")))
            c["managed"] = bool(m)
            c["definition_id"] = m.get("definition_id") if m else None
            c["definition_name"] = m.get("definition_name") if m else None
            # Provenance: Kometa labels its collections ('Kometa'/'PMM'); smart
            # (filter-based) collections are never SoulSync's either.
            labels = {str(x).strip().lower() for x in (c.get("labels") or [])}
            c["kometa"] = bool(labels & kometa_labels) and not c["managed"]
        cols.sort(key=lambda c: (c["managed"], not c.get("kometa"), (c.get("name") or "").casefold()))
        return jsonify({"ok": True, "server": src.server_name, "collections": cols})

    @bp.route("/collections/server/delete", methods=["POST"])
    def collections_server_delete():
        """START a background bulk-delete of server collections (a Kometa purge
        can be thousands — far too long for one request). Returns {ok, total}
        immediately; progress streams over the 'collections:cleanup' socket
        event (~1/s) with GET .../delete/status as the polling fallback.
        Managed deletes clear their ledger row; definitions are never touched."""
        from . import get_video_db
        from core.video.collections.server_cleanup import start_delete
        d = request.get_json(silent=True) or {}
        ids = d.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids are required"}), 400
        r = start_delete(get_video_db(), ids)
        if not r.get("ok"):
            already = "already running" in (r.get("error") or "")
            return jsonify(r), (409 if already else 400)
        return jsonify(r)

    @bp.route("/collections/server/delete/status", methods=["GET"])
    def collections_server_delete_status():
        from core.video.collections.server_cleanup import status
        return jsonify(status())

    @bp.route("/collections/server/adopt", methods=["POST"])
    def collections_server_adopt():
        """Bring existing server collections under SoulSync management (the
        Kometa migration path): membership snapshot + ledger binding. Append
        sync mode + the server's own poster are kept — adoption changes who
        manages the collection, not what it looks like."""
        from . import get_video_db
        from core.video.collections.server_cleanup import adopt_collections
        d = request.get_json(silent=True) or {}
        items = d.get("items") or []
        if not isinstance(items, list) or not items:
            return jsonify({"ok": False, "error": "items are required"}), 400
        r = adopt_collections(get_video_db(), items)
        if not r.get("ok"):
            return jsonify(r), 400
        return jsonify(r)
