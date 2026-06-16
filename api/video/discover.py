"""Video discover API — browse TMDB (movies/TV the user doesn't own yet).

GET /api/video/discover/hero          → trending titles w/ backdrops (slideshow)
GET /api/video/discover/genres        → {movie:[{id,name}], show:[{id,name}]}
GET /api/video/discover/list?...      → one shelf/grid of items, e.g.
      ?key=trending                   → trending movies + shows
      ?key=<curated>&page=            → a canned list (popular_movies, top_shows…)
      ?kind=movie|show&genre=&year=&decade=&sort=&page=   → a filtered browse

Items are annotated with ``library_id`` when already owned (so the card links to
the owned detail, not the TMDB preview). Reads only the enrichment engine +
video.db; isolated from the music API.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.discover")


def register_routes(bp):
    @bp.route("/discover/hero", methods=["GET"])
    def video_discover_hero():
        """A few trending titles that have a backdrop — drives the hero slideshow."""
        from core.video.enrichment.engine import get_video_enrichment_engine
        try:
            items = [x for x in (get_video_enrichment_engine().trending() or [])
                     if x.get("backdrop")][:6]
        except Exception:
            logger.exception("discover hero failed")
            items = []
        return jsonify({"items": items})

    @bp.route("/discover/genres", methods=["GET"])
    def video_discover_genres():
        """Genre id→name maps for both kinds (powers the genre rails + filter)."""
        from core.video.enrichment.engine import get_video_enrichment_engine
        eng = get_video_enrichment_engine()
        try:
            return jsonify({"movie": eng.genre_list("movie"), "show": eng.genre_list("show")})
        except Exception:
            logger.exception("discover genres failed")
            return jsonify({"movie": [], "show": []})

    @bp.route("/discover/list", methods=["GET"])
    def video_discover_list():
        """One shelf (rail) or one page of a filtered browse — see module docstring."""
        from core.video.enrichment.engine import get_video_enrichment_engine
        eng = get_video_enrichment_engine()
        try:
            page = max(1, int(request.args.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        key = (request.args.get("key") or "").strip()
        try:
            if key == "trending":
                items = eng.trending()
            elif key:
                items = eng.discover_curated(key, page=page)
            else:
                items = eng.discover_filter(
                    request.args.get("kind", "movie"),
                    genre=(request.args.get("genre") or None),
                    year=(request.args.get("year") or None),
                    decade=(request.args.get("decade") or None),
                    sort_by=(request.args.get("sort") or "popularity.desc"),
                    page=page)
            return jsonify({"items": items or [], "page": page})
        except Exception:
            logger.exception("discover list failed (key=%s)", key)
            return jsonify({"items": [], "page": page})
