"""Video enrichment API — mirrors the music enrichment endpoints so the shared
Manage-Workers modal can drive video workers by pointing at /api/video/...

  GET  /api/video/enrichment/services
  GET  /api/video/enrichment/<service>/status
  POST /api/video/enrichment/<service>/pause | /resume
  GET  /api/video/enrichment/<service>/breakdown
  GET  /api/video/enrichment/<service>/unmatched?kind=&status=&q=&limit=&offset=
  POST /api/video/enrichment/<service>/retry   {kind, scope, item_id}
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.enrichment")


def _int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def register_routes(bp):
    def engine():
        from core.video.enrichment.engine import get_video_enrichment_engine
        return get_video_enrichment_engine()

    def _yt_enricher():
        from core.video.youtube_enrichment import get_youtube_date_enricher
        return get_youtube_date_enricher()

    @bp.route("/enrichment/services", methods=["GET"])
    def video_enrichment_services():
        try:
            return jsonify({"services": engine().services()})
        except Exception:
            logger.exception("video enrichment services failed")
            return jsonify({"services": []})

    @bp.route("/enrichment/coverage", methods=["GET"])
    def video_enrichment_coverage():
        """TMDB/TVDB match + enrichment coverage for movies + shows — drives the dashboard
        Studio cards' coverage bars (Overlay/Collection Studio need enriched metadata)."""
        from . import get_video_db
        try:
            return jsonify(get_video_db().enrichment_coverage())
        except Exception:
            logger.exception("video enrichment coverage failed")
            return jsonify({"movies": {}, "shows": {}})

    @bp.route("/enrichment/resync-details", methods=["POST"])
    def video_enrichment_resync_details():
        """Re-pull full TMDB metadata for every on-server, matched title — how an existing
        library gets the COMPLETE multi-company studio/network data (the media-server scan
        only exposes one company). Resets details_synced=0; the detail-sync worker drains it
        in the background (watch the TMDB worker in the enrichment sidebar). Body: {kind?}."""
        from . import get_video_db
        try:
            kind = str((request.get_json(silent=True) or {}).get("kind") or "all")
            if kind not in ("all", "movie", "show"):
                kind = "all"
            queued = get_video_db().queue_detail_resync(kind)
            engine()   # ensure the enrichment engine (detail-sync worker) is up to drain it
            return jsonify({"success": True, "queued": queued})
        except Exception:
            logger.exception("video enrichment resync-details failed")
            return jsonify({"success": False, "error": "Failed to queue the re-sync"}), 500

    @bp.route("/enrichment/config", methods=["GET"])
    def video_enrichment_config():
        from . import get_video_db
        db = get_video_db()
        return jsonify({
            "tmdb_api_key": db.get_setting("tmdb_api_key") or "",
            "tvdb_api_key": db.get_setting("tvdb_api_key") or "",
            "omdb_api_key": db.get_setting("omdb_api_key") or "",
            # Backfill-worker keys (free, optional) + no-key toggles.
            "fanart_api_key": db.get_setting("fanart_api_key") or "",
            "opensubtitles_api_key": db.get_setting("opensubtitles_api_key") or "",
            "trakt_api_key": db.get_setting("trakt_api_key") or "",
            "mdblist_api_key": db.get_setting("mdblist_api_key") or "",
            "ryd_enabled": (db.get_setting("ryd_enabled") or "1") == "1",
            "sponsorblock_enabled": (db.get_setting("sponsorblock_enabled") or "1") == "1",
            "dearrow_enabled": (db.get_setting("dearrow_enabled") or "1") == "1",
            "tvmaze_enabled": (db.get_setting("tvmaze_enabled") or "1") == "1",
            "anilist_enabled": (db.get_setting("anilist_enabled") or "0") == "1",
            "wikidata_enabled": (db.get_setting("wikidata_enabled") or "1") == "1",
            "billboard_autoplay": (db.get_setting("billboard_autoplay") or "1") == "1",
            "watch_region": (db.get_setting("watch_region") or "US").upper(),
        })

    @bp.route("/prefs", methods=["GET"])
    def video_prefs():
        # Lightweight UI prefs for the detail page (no API keys).
        from . import get_video_db
        db = get_video_db()
        return jsonify({
            "billboard_autoplay": (db.get_setting("billboard_autoplay") or "1") == "1",
            "watch_region": (db.get_setting("watch_region") or "US").upper(),
        })

    @bp.route("/enrichment/config", methods=["POST"])
    def video_enrichment_config_save():
        from . import get_video_db
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        keys_changed = False

        def put_key(field):
            nonlocal keys_changed
            if field in body:
                val = body.get(field) or ""
                if val != (db.get_setting(field) or ""):
                    keys_changed = True
                db.set_setting(field, val)

        put_key("tmdb_api_key")
        put_key("tvdb_api_key")
        put_key("fanart_api_key")
        put_key("opensubtitles_api_key")
        put_key("trakt_api_key")
        put_key("mdblist_api_key")
        # No-key worker on/off toggles (read live by the worker — no rebuild needed).
        for flag in ("ryd_enabled", "sponsorblock_enabled", "dearrow_enabled",
                     "tvmaze_enabled", "anilist_enabled", "wikidata_enabled"):
            if flag in body:
                db.set_setting(flag, "1" if body.get(flag) else "0")
        if "billboard_autoplay" in body:
            db.set_setting("billboard_autoplay", "1" if body.get("billboard_autoplay") else "0")
        if "watch_region" in body:
            region = (body.get("watch_region") or "US").strip().upper()[:2] or "US"
            db.set_setting("watch_region", region)
        if "omdb_api_key" in body:
            new_key = body.get("omdb_api_key") or ""
            changed = new_key != (db.get_setting("omdb_api_key") or "")
            db.set_setting("omdb_api_key", new_key)
            keys_changed = keys_changed or changed
            # A new/changed OMDb key → re-try every title that still has no rating
            # (covers items wrongly marked 'synced' during a prior bad-key run).
            if new_key and changed:
                try:
                    for kind in ("movie", "show"):
                        db.enrichment_retry("omdb", kind, scope="failed")
                except Exception:
                    logger.exception("video enrichment: omdb re-try reset failed")
        # Only rebuild the workers when an API KEY actually changed — a prefs-only
        # save (autoplay/region) must not churn the running enrichment engine
        # (that restart was re-logging the OMDb limit warning on every save).
        if keys_changed:
            try:
                from core.video.enrichment.engine import rebuild_video_enrichment_engine
                rebuild_video_enrichment_engine()
            except Exception:
                logger.exception("video enrichment: engine rebuild after key change failed")
        return jsonify({"status": "saved"})

    @bp.route("/enrichment/<service>/status", methods=["GET"])
    def video_enrichment_status(service):
        if service == "youtube":   # the standalone date enricher (not an engine worker)
            return jsonify(_yt_enricher().stats())
        w = engine().worker(service)
        if not w:
            return jsonify({"error": "unknown service"}), 404
        return jsonify(w.get_stats())

    @bp.route("/enrichment/<service>/pause", methods=["POST"])
    def video_enrichment_pause(service):
        if service == "youtube":
            _yt_enricher().pause()
            return jsonify({"status": "paused"})
        w = engine().worker(service)
        if not w:
            return jsonify({"error": "unknown service"}), 404
        w.pause()
        return jsonify({"status": "paused"})

    @bp.route("/enrichment/<service>/resume", methods=["POST"])
    def video_enrichment_resume(service):
        if service == "youtube":
            _yt_enricher().resume()
            return jsonify({"status": "running"})
        w = engine().worker(service)
        if not w:
            return jsonify({"error": "unknown service"}), 404
        w.resume()
        return jsonify({"status": "running"})

    @bp.route("/enrichment/priority", methods=["GET", "POST"])
    def video_enrichment_priority():
        from . import get_video_db
        db = get_video_db()
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            kind = body.get("priority") or ""
            if kind not in ("", "movie", "show"):
                return jsonify({"error": "bad priority"}), 400
            db.set_setting("enrichment_priority", kind)
            return jsonify({"success": True, "priority": kind})
        return jsonify({"priority": db.get_setting("enrichment_priority") or ""})

    @bp.route("/enrichment/<service>/test", methods=["POST"])
    def video_enrichment_test(service):
        w = engine().worker(service)
        if not w:
            return jsonify({"success": False, "error": "unknown service"}), 404
        try:
            ok, msg = w.client.test()
            return jsonify({"success": bool(ok), "message": msg, "error": None if ok else msg})
        except Exception:
            logger.exception("video enrichment test failed for %s", service)
            return jsonify({"success": False, "error": "Test failed"})

    @bp.route("/enrichment/<service>/breakdown", methods=["GET"])
    def video_enrichment_breakdown(service):
        from . import get_video_db
        return jsonify({"service": service, "breakdown": get_video_db().enrichment_breakdown(service)})

    @bp.route("/enrichment/<service>/unmatched", methods=["GET"])
    def video_enrichment_unmatched(service):
        from . import get_video_db
        kind = request.args.get("kind", "movie")
        res = get_video_db().enrichment_unmatched(
            service, kind,
            status=request.args.get("status", "not_found"),
            search=request.args.get("q") or None,
            limit=_int(request.args.get("limit"), 50),
            offset=_int(request.args.get("offset"), 0))
        res.update({"service": service, "kind": kind})
        return jsonify(res)

    @bp.route("/enrichment/retry-all-failed", methods=["POST"])
    def video_enrichment_retry_all_failed():
        """Global re-queue: reset every failed/not_found item across ALL workers and
        kinds (one-click recovery after an outage). Returns the total re-queued."""
        from . import get_video_db
        return jsonify({"success": True, "reset": get_video_db().retry_all_failed()})

    @bp.route("/enrichment/<service>/retry", methods=["POST"])
    def video_enrichment_retry(service):
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        n = get_video_db().enrichment_retry(
            service, body.get("kind", "movie"),
            scope=body.get("scope", "failed"), item_id=body.get("item_id"))
        return jsonify({"success": True, "reset": n})

    # ── manual match editor (Manage panel "Matches" section) ──────────────────
    # GET is open (metadata read, like the detail GET); the POSTs mutate the
    # library and inherit the blueprint's admin gate on /api/video/enrichment.

    @bp.route("/enrichment/matches/<kind>/<int:item_id>", methods=["GET"])
    def video_item_matches(kind, item_id):
        from . import get_video_db
        try:
            matches = get_video_db().item_matches(kind, item_id)
            if matches is None:
                return jsonify({"error": "unknown kind"}), 400
            return jsonify({"matches": matches})
        except Exception:
            logger.exception("video item matches failed for %s %s", kind, item_id)
            return jsonify({"error": "Failed to load matches"}), 500

    @bp.route("/enrichment/matches/<kind>/<int:item_id>/search", methods=["POST"])
    def video_match_search(kind, item_id):
        """Candidate search for a manual re-match. {service, query} → results
        straight from the service's own search endpoint (no scoring — the user
        is the matcher here)."""
        body = request.get_json(silent=True) or {}
        service = (body.get("service") or "").strip().lower()
        query = (body.get("query") or "").strip()
        if service not in ("tmdb", "tvdb") or not query:
            return jsonify({"error": "service (tmdb|tvdb) and query are required"}), 400
        w = engine().workers.get(service)
        client = getattr(w, "client", None)
        if not client or not hasattr(client, "search_candidates"):
            return jsonify({"error": "Service not configured"}), 503
        try:
            return jsonify({"results": client.search_candidates(kind, query)})
        except Exception:
            logger.exception("video match search failed (%s: %r)", service, query)
            return jsonify({"error": "Search failed — try again"}), 502

    @bp.route("/enrichment/matches/<kind>/<int:item_id>/apply", methods=["POST"])
    def video_match_apply(kind, item_id):
        """Re-point (or clear) one service's match. {service, external_id|null}.
        The DB layer resets everything derived from the old match; the already-
        running workers then re-enrich by the new id in the background."""
        import re as _re
        body = request.get_json(silent=True) or {}
        service = (body.get("service") or "").strip().lower()
        external_id = body.get("external_id")
        if external_id is not None:
            if service == "imdb":
                external_id = str(external_id).strip()
                if not _re.fullmatch(r"tt\d{5,10}", external_id):
                    return jsonify({"error": "IMDb id must look like tt0944947"}), 400
            else:
                try:
                    external_id = int(external_id)
                except (TypeError, ValueError):
                    return jsonify({"error": "external_id must be numeric"}), 400
        from . import get_video_db
        try:
            ok = get_video_db().rematch_item(kind, item_id, service, external_id)
            if not ok:
                return jsonify({"success": False, "error": "Unknown item or service"}), 400
            return jsonify({"success": True})
        except Exception:
            logger.exception("video match apply failed for %s %s (%s)", kind, item_id, service)
            return jsonify({"success": False, "error": "Failed to update the match"}), 500
