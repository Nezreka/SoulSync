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

    @bp.route("/discover/morelike", methods=["GET"])
    def video_discover_morelike():
        """'More like <owned title>' rails — TMDB recommendations seeded from a few
        random titles you already own (interleaved movie/show, max 3 rails)."""
        from . import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None
        db = get_video_db()
        eng = get_video_enrichment_engine()
        try:
            seeds = db.random_owned_titles(2, srv)
            movies = [s for s in seeds if s["kind"] == "movie"]
            shows = [s for s in seeds if s["kind"] == "show"]
            ordered = []
            while (movies or shows) and len(ordered) < 3:
                if movies:
                    ordered.append(movies.pop(0))
                if shows and len(ordered) < 3:
                    ordered.append(shows.pop(0))
            rails = []
            for s in ordered:
                items = [it for it in eng.recommendations(s["kind"], s["tmdb_id"])
                         if it.get("tmdb_id") != s["tmdb_id"]]
                if len(items) >= 4:
                    rails.append({"title": "More like " + s["title"], "items": items[:30]})
            return jsonify({"rails": rails})
        except Exception:
            logger.exception("discover morelike failed")
            return jsonify({"rails": []})

    @bp.route("/discover/gaps", methods=["GET"])
    def video_discover_gaps():
        """'What am I missing?' rails — franchises you've started but not finished, and
        more from the directors/creators you own the most. Powered by the gap engine."""
        from . import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        from core.video.discovery_gaps import collection_gaps, filmography_gaps
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None
        db = get_video_db()
        eng = get_video_enrichment_engine()
        try:
            # Lazy collection-id backfill: movies matched before the collection column
            # exists have no franchise id. Fill a small batch each load (self-healing);
            # isolated so a backfill hiccup never breaks the gap rails.
            try:
                for mv in db.movies_missing_collection(srv, limit=20):
                    coll = eng.movie_collection(mv["tmdb_id"])
                    if coll is not None:
                        db.set_movie_collection(mv["id"], coll.get("id"), coll.get("name"))
            except Exception:
                logger.exception("collection-id backfill batch failed")

            owned = db.owned_movie_tmdb_ids(srv)
            rails = []
            # Complete your collections — top franchises you've started, missing entries.
            for coll in db.owned_movie_collections(srv, limit=8):
                missing = collection_gaps(owned, eng.collection(coll["collection_id"]))
                if missing:
                    name = (coll.get("name") or "Collection").strip()
                    rails.append({"title": "Complete the " + name, "kind": "collection",
                                  "items": missing[:30]})
            # More from the people you own the most (directors / creators).
            for person in db.top_owned_people(min_titles=2, limit=6, server_source=srv):
                p = eng.person_detail(person["tmdb_id"])
                if not p:
                    continue
                missing = filmography_gaps(owned, p.get("credits") or [],
                                           kinds=("movie",), min_vote_count=50, limit=30)
                if len(missing) >= 3:
                    rails.append({"title": "More from " + person["name"], "kind": "person",
                                  "items": missing})
            return jsonify({"rails": rails})
        except Exception:
            logger.exception("discover gaps failed")
            return jsonify({"rails": []})

    @bp.route("/discover/trailer", methods=["GET"])
    def video_discover_trailer():
        """Best YouTube trailer {key,name} for a tmdb title (hero 'Trailer' button)."""
        from core.video.enrichment.engine import get_video_enrichment_engine
        kind = request.args.get("kind", "movie")
        try:
            tmdb_id = int(request.args.get("tmdb_id"))
        except (TypeError, ValueError):
            return jsonify({"trailer": None})
        try:
            tr = get_video_enrichment_engine().trailer(kind, tmdb_id)
        except Exception:
            logger.exception("discover trailer failed")
            tr = None
        return jsonify({"trailer": tr or None})

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
        providers = request.args.get("providers") or None
        sort = request.args.get("sort") or "popularity.desc"
        lang = (request.args.get("lang") or "").strip() or None       # original-language filter
        hide_owned = (request.args.get("hide_owned") or "") in ("1", "true", "yes")

        def fetch(p):
            if key == "trending":
                return eng.trending()
            if key:
                return eng.discover_curated(key, page=p)
            return eng.discover_filter(kind, genre=genre, year=year, decade=decade,
                                       providers=providers, sort_by=sort, page=p, language=lang)

        try:
            items, seen = [], set()
            # Hiding owned + a huge library means most popular titles are already owned, so
            # page DEEPER and drop owned server-side until the rail has enough un-owned to
            # look full (instead of the client CSS-hiding most of a 2-page batch to nothing).
            target = 24 if hide_owned else 0
            max_pages = 8 if hide_owned else pages
            for offset in range(max_pages):
                batch = fetch(page + offset) or []
                for it in batch:
                    dk = (it.get("kind"), it.get("tmdb_id"))
                    if dk in seen:
                        continue
                    seen.add(dk)
                    if hide_owned and it.get("library_id") is not None:
                        continue
                    items.append(it)
                if key == "trending" or not batch:
                    break        # trending is a fixed list; empty batch = TMDB ran out
                if target and len(items) >= target:
                    break        # enough un-owned collected
                if not hide_owned and offset + 1 >= pages:
                    break        # normal mode: respect the requested page count
            return jsonify({"items": items, "page": page})
        except Exception:
            logger.exception("discover list failed (key=%s)", key)
            return jsonify({"items": [], "page": page})
