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
