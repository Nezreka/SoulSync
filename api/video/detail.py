"""Video detail payloads (drill-in pages).

GET /api/video/detail/show/<id>   → show + seasons→episodes tree (owned roll-ups)
GET /api/video/detail/movie/<id>  → movie + owned/file info

Reads only video.db; isolated from the music API.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.detail")


def register_routes(bp):
    @bp.route("/monitor", methods=["POST"])
    def video_set_monitor():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("id")
        if kind not in ("movie", "show") or not isinstance(item_id, int):
            return jsonify({"error": "bad request"}), 400
        ok = get_video_db().set_monitored(kind, item_id, bool(body.get("monitored")))
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True, "monitored": bool(body.get("monitored"))})

    # ── Manage sidebar: metadata edits + field locks + watched ────────────────
    @bp.route("/detail/<kind>/<int:item_id>/metadata", methods=["PUT"])
    def video_edit_metadata(kind, item_id):
        """Apply user edits (title/sort/year/rating/genres/summary/tagline…).
        Writes locally + auto-locks the fields, then pushes to Plex/Jellyfin
        with the server's own field locks set."""
        from core.video import metadata as med

        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        changes = (request.get_json(silent=True) or {}).get("changes")
        if not isinstance(changes, dict) or not changes:
            return jsonify({"error": "no changes"}), 400
        try:
            res = med.edit_item(get_video_db(), kind, item_id, changes)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/lock", methods=["POST"])
    def video_field_lock(kind, item_id):
        """Lock or release one field. Releasing hands it back to the server:
        the next scan re-adopts the server's value."""
        from core.video import metadata as med

        from . import get_video_db
        body = request.get_json(silent=True) or {}
        field = body.get("field")
        if kind not in ("movie", "show") or not field:
            return jsonify({"error": "bad request"}), 400
        db = get_video_db()
        if body.get("locked"):
            locks = db.set_field_lock(kind, item_id, field, True)
            if locks is None:
                return jsonify({"error": "unknown item or field"}), 404
            return jsonify({"ok": True, "locked": locks})
        res = med.release_lock(db, kind, item_id, field)
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/watched", methods=["POST"])
    def video_set_watched(kind, item_id):
        """Played/unplayed toggle — local watch state + server markPlayed."""
        from core.video import metadata as med

        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        watched = bool((request.get_json(silent=True) or {}).get("watched"))
        res = med.set_watched(get_video_db(), kind, item_id, watched)
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/history", methods=["GET"])
    def video_title_history(kind, item_id):
        """This title's permanent acquisition history (arr-parity P9): grabs,
        imports, upgrades, failures — matched under both the library and TMDB
        identities the title may have been grabbed as."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        db = get_video_db()
        detail = db.movie_detail(item_id) if kind == "movie" else db.show_detail(item_id)
        if not detail:
            return jsonify({"error": "not found"}), 404
        rows = db.title_download_history(kind, library_id=item_id,
                                         tmdb_id=detail.get("tmdb_id"))
        return jsonify({"success": True, "history": rows})

    @bp.route("/detail/show/<int:show_id>", methods=["GET"])
    def video_show_detail(show_id):
        from . import get_video_db
        data = get_video_db().show_detail(show_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/movie/<int:movie_id>", methods=["GET"])
    def video_movie_detail(movie_id):
        from . import get_video_db
        data = get_video_db().movie_detail(movie_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/show/<int:show_id>/sync", methods=["POST"])
    def video_show_sync(show_id):
        """Per-show Synchronize: re-read THIS show from the server and
        reconcile local rows (adds, updates, prunes vanished episodes; removes
        the show if the server verifiably no longer has it). Admin-gated via
        the blueprint's /sync write rule. Synchronous — a single show reads in
        seconds, and the response carries what changed."""
        from . import get_video_db
        try:
            from core.video.show_sync import ShowSyncError, sync_show
            res = sync_show(get_video_db(), show_id)
            return jsonify({"success": True, **res})
        except ShowSyncError as e:
            return jsonify({"success": False, "error": str(e)}), 409
        except Exception:
            logger.exception("show sync failed for %s", show_id)
            return jsonify({"success": False, "error": "Sync failed — see app.log"}), 500

    @bp.route("/detail/show/<int:show_id>/refresh-art", methods=["POST"])
    def video_show_refresh_art(show_id):
        """Lazy on-view backfill: pull missing season posters / episode art from
        TMDB and cache them. Best-effort — never errors the page."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_show_art(show_id)
        except Exception:
            logger.exception("refresh-art failed for show %s", show_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)

    @bp.route("/detail/movie/<int:movie_id>/refresh-art", methods=["POST"])
    def video_movie_refresh_art(movie_id):
        """Lazy on-view backfill for a movie (cast / genres / backdrop / ratings)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_movie_art(movie_id)
        except Exception:
            logger.exception("refresh-art failed for movie %s", movie_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)

    @bp.route("/tmdb/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_tmdb_detail(kind, tmdb_id):
        """Full detail for a TMDB title not in the library (the search → detail
        view). May return {redirect:{source,kind,id}} if it's actually owned."""
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_detail(kind, tmdb_id)
        except Exception:
            logger.exception("tmdb detail failed for %s %s", kind, tmdb_id)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/tmdb/show/<int:tv_id>/season/<int:season_number>", methods=["GET"])
    def video_tmdb_season(tv_id, season_number):
        """Lazy per-season episodes for a TMDB (un-owned) show detail."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_season(tv_id, season_number)
        except Exception:
            logger.exception("tmdb season failed for %s S%s", tv_id, season_number)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/episode/<int:tmdb_id>/<int:season>/<int:episode>", methods=["GET"])
    def video_episode_extra(tmdb_id, season, episode):
        """Episode expand: guest stars + bigger still (by the SHOW's tmdb id)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().episode_extra(tmdb_id, season, episode)
        except Exception:
            logger.exception("episode extra failed for %s S%sE%s", tmdb_id, season, episode)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/person/<int:tmdb_id>", methods=["GET"])
    def video_person_detail(tmdb_id):
        """In-app person page: bio + filmography (each credit annotated owned/not)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().person_detail(tmdb_id)
        except Exception:
            logger.exception("person detail failed for %s", tmdb_id)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/detail/<kind>/<int:item_id>/extras", methods=["GET"])
    def video_detail_extras(kind, item_id):
        """Live TMDB extras (trailer / where-to-watch / similar) for the detail page."""
        if kind not in ("movie", "show"):
            return jsonify({}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            return jsonify(get_video_enrichment_engine().item_extras(kind, item_id))
        except Exception:
            logger.exception("extras failed for %s %s", kind, item_id)
            return jsonify({})
