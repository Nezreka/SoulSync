"""Video search API (in-app, isolated).

  GET /api/video/search?q=...   → TMDB multi-search (movies / shows / people),
                                  movie/show results annotated with library_id
                                  if already owned.

Everything resolves back into SoulSync — results link to the library detail
(owned) or the TMDB-backed detail (not owned); people open the in-app person
page. Reads only the video engine + video.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.search")


def register_routes(bp):
    @bp.route("/search", methods=["GET"])
    def video_search():
        """Fast multi-search (movies / shows / people). Studios are a SEPARATE call
        (/search/studios) so their slower film-count ranking never blocks this — the UI
        paints titles instantly and streams studios in after."""
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"results": [], "query": ""})
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            results = get_video_enrichment_engine().search(q) or []
        except Exception:
            logger.exception("video search failed for %r", q)
            results = []
        return jsonify({"results": results, "query": q})

    @bp.route("/search/studios", methods=["GET"])
    def video_search_studios():
        """Studio (production-company) search — its own endpoint so the main search paints
        without waiting on the per-studio film-count ranking."""
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"results": [], "query": ""})
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            results = get_video_enrichment_engine().company_search(q) or []
        except Exception:
            logger.exception("video studio search failed for %r", q)
            results = []
        return jsonify({"results": results, "query": q})

    _STUDIO_SORTS = {"primary_release_date.desc", "primary_release_date.asc",
                     "popularity.desc", "vote_average.desc", "revenue.desc"}

    def _studio_sort():
        s = request.args.get("sort") or "primary_release_date.desc"
        return s if s in _STUDIO_SORTS else "primary_release_date.desc"

    @bp.route("/studio/presets", methods=["GET"])
    def video_studio_presets():
        """Curated studio families (Disney = Pixar + Marvel + Lucasfilm…) for the watchlist
        picker. Each member carries its logo + whether it's already followed, so the picker
        toggles members individually — a family is just a bulk-add over per-studio follows,
        never a forced bundle."""
        from core.video.studio_presets import studio_presets, preset_member_ids
        from . import get_video_db
        try:
            presets = studio_presets()             # logos are baked in → no TMDB round-trips
            try:
                followed = get_video_db().watchlist_state("studio", preset_member_ids())
            except Exception:   # noqa: BLE001 - picker still works without follow state
                followed = {}
            for p in presets:
                for m in p["members"]:
                    m["followed"] = bool(followed.get(m["tmdb_id"]))
            return jsonify({"success": True, "presets": presets})
        except Exception:
            logger.exception("studio presets failed")
            return jsonify({"success": False, "presets": []}), 500

    @bp.route("/studio/<int:company_id>", methods=["GET"])
    def video_studio_detail(company_id):
        """A studio's header (name / logo / about / HQ) + its first page of movies. Powers the
        Studio detail page — a collection of films, not a movie or a show."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            eng = get_video_enrichment_engine()
            detail = eng.company_detail(company_id)
            if not detail:
                return jsonify({"success": False, "error": "not found"}), 404
            return jsonify({"success": True, "studio": detail,
                            "movies": eng.company_movies(company_id, page=1, sort=_studio_sort())})
        except Exception:
            logger.exception("studio detail failed for %s", company_id)
            return jsonify({"success": False, "error": "failed"}), 500

    @bp.route("/studio/<int:company_id>/movies", methods=["GET"])
    def video_studio_movies(company_id):
        """Paged movies for a studio (grid infinite-scroll). ?page= &sort=."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            page = max(1, int(request.args.get("page") or 1))
            return jsonify({"success": True,
                            **get_video_enrichment_engine().company_movies(company_id, page=page, sort=_studio_sort())})
        except (ValueError, TypeError):
            return jsonify({"success": False, "results": [], "total_pages": 0}), 400
        except Exception:
            logger.exception("studio movies failed for %s", company_id)
            return jsonify({"success": False, "results": [], "total_pages": 0}), 500

    @bp.route("/trending", methods=["GET"])
    def video_trending():
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            results = get_video_enrichment_engine().trending()
        except Exception:
            logger.exception("video trending failed")
            results = []
        return jsonify({"results": results})
