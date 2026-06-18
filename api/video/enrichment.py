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
            "ryd_enabled": (db.get_setting("ryd_enabled") or "1") == "1",
            "sponsorblock_enabled": (db.get_setting("sponsorblock_enabled") or "1") == "1",
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
        # No-key worker on/off toggles (read live by the worker — no rebuild needed).
        for flag in ("ryd_enabled", "sponsorblock_enabled"):
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

    @bp.route("/enrichment/<service>/retry", methods=["POST"])
    def video_enrichment_retry(service):
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        n = get_video_db().enrichment_retry(
            service, body.get("kind", "movie"),
            scope=body.get("scope", "failed"), item_id=body.get("item_id"))
        return jsonify({"success": True, "reset": n})
