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

    @bp.route("/discover/taste", methods=["GET"])
    def video_discover_taste():
        """The user's most-owned genres (movies + shows) → personalized rails."""
        from . import get_video_db
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None
        db = get_video_db()
        try:
            return jsonify({"movie": db.top_owned_genres("movie", srv, 6),
                            "show": db.top_owned_genres("show", srv, 6)})
        except Exception:
            logger.exception("discover taste failed")
            return jsonify({"movie": [], "show": []})

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
        """One shelf (rail) or one page of a filtered browse — see module docstring.

        ``pages`` (1–3, default 1) fetches that many consecutive TMDB pages and
        concatenates them (deduped) in one response — so a rail can show ~40 items
        and still look full after 'Hide owned' drops the ones you have."""
        from core.video.enrichment.engine import get_video_enrichment_engine
        eng = get_video_enrichment_engine()
        try:
            page = max(1, int(request.args.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            pages = min(3, max(1, int(request.args.get("pages", 1) or 1)))
        except (TypeError, ValueError):
            pages = 1
        key = (request.args.get("key") or "").strip()
        kind = request.args.get("kind", "movie")
        genre = request.args.get("genre") or None
        year = request.args.get("year") or None
        decade = request.args.get("decade") or None
        sort = request.args.get("sort") or "popularity.desc"

        def fetch(p):
            if key == "trending":
                return eng.trending()
            if key:
                return eng.discover_curated(key, page=p)
            return eng.discover_filter(kind, genre=genre, year=year, decade=decade,
                                       sort_by=sort, page=p)

        try:
            items, seen = [], set()
            for p in range(page, page + pages):
                for it in (fetch(p) or []):
                    dk = (it.get("kind"), it.get("tmdb_id"))
                    if dk in seen:
                        continue
                    seen.add(dk)
                    items.append(it)
                if key == "trending":
                    break        # trending is a fixed list — extra pages just repeat it
            return jsonify({"items": items, "page": page})
        except Exception:
            logger.exception("discover list failed (key=%s)", key)
            return jsonify({"items": [], "page": page})
