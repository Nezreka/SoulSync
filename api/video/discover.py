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

    @bp.route("/discover/foryou", methods=["GET"])
    def video_discover_foryou():
        """A single 'Recommended for you' wall blended from many owned titles — a title
        recommended by more of your library ranks higher (consensus)."""
        from . import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        from core.video.discovery_recs import blend_recommendations
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None
        db = get_video_db()
        eng = get_video_enrichment_engine()
        try:
            seeds = db.random_owned_titles(6, srv)   # up to 6 movies + 6 shows
            seed_ids = [s["tmdb_id"] for s in seeds if s.get("tmdb_id")]
            rec_lists = [eng.recommendations(s["kind"], s["tmdb_id"])
                         for s in seeds if s.get("tmdb_id")]
            items = blend_recommendations(rec_lists, exclude_ids=seed_ids, limit=40)
            return jsonify({"items": items})
        except Exception:
            logger.exception("discover foryou failed")
            return jsonify({"items": []})

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
            ignored = db.ignored_keys()
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
                if ignored:
                    missing = [m for m in missing
                               if f"{m.get('kind')}:{m.get('tmdb_id')}" not in ignored]
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

    @bp.route("/discover/ignore", methods=["GET", "POST"])
    def video_discover_ignore():
        """The Discover 'Not interested' list. GET -> {items}. POST
        {action:'add'|'remove', kind, tmdb_id, title?, year?, poster?}."""
        from . import get_video_db
        db = get_video_db()
        try:
            if request.method == "GET":
                return jsonify({"items": db.list_ignored()})
            body = request.get_json(silent=True) or {}
            kind, tmdb_id = body.get("kind"), body.get("tmdb_id")
            if body.get("action") == "remove":
                db.remove_ignored(kind, tmdb_id)
                return jsonify({"success": True})
            ok = db.add_ignored(kind, tmdb_id, body.get("title"), body.get("year"), body.get("poster"))
            return jsonify({"success": ok})
        except Exception:
            logger.exception("discover ignore failed")
            return jsonify({"success": False, "items": []})

    @bp.route("/discover/languages", methods=["GET", "POST"])
    def video_discover_languages():
        """Get/set the preferred original-languages for general rails (ISO-639-1 codes).
        POST {languages: ['en','ko']} (or 'en,ko'); GET returns the current list."""
        from . import get_video_db
        db = get_video_db()
        try:
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                langs = body.get("languages")
                if isinstance(langs, list):
                    val = ",".join(str(c).strip().lower() for c in langs if str(c).strip())
                else:
                    val = ",".join(c.strip().lower() for c in str(langs or "").split(",") if c.strip())
                db.set_setting("discover_languages", val or "en")
                return jsonify({"success": True,
                                "languages": [c for c in (val or "en").split(",") if c]})
            raw = db.get_setting("discover_languages", "en") or "en"
            return jsonify({"languages": [c.strip() for c in raw.split(",") if c.strip()]})
        except Exception:
            logger.exception("discover languages get/set failed")
            return jsonify({"languages": ["en"]})

    @bp.route("/discover/providers-pref", methods=["GET", "POST"])
    def video_discover_providers_pref():
        """The user's subscribed streaming services (TMDB provider ids) — drives the
        'On your streaming services' rail. POST {providers:[8,9]} (or '8,9'); GET returns them."""
        from . import get_video_db
        db = get_video_db()
        try:
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                p = body.get("providers")
                if isinstance(p, list):
                    val = ",".join(str(c).strip() for c in p if str(c).strip())
                else:
                    val = ",".join(c.strip() for c in str(p or "").split(",") if c.strip())
                db.set_setting("discover_providers", val)
                return jsonify({"success": True, "providers": [c for c in val.split(",") if c]})
            raw = db.get_setting("discover_providers", "") or ""
            return jsonify({"providers": [c.strip() for c in raw.split(",") if c.strip()]})
        except Exception:
            logger.exception("discover providers-pref failed")
            return jsonify({"providers": []})

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
        if providers and "," in providers:
            providers = providers.replace(",", "|")   # TMDB with_watch_providers OR-join
        sort = request.args.get("sort") or "popularity.desc"
        lang = (request.args.get("lang") or "").strip() or None       # explicit (foreign rail)
        hide_owned = (request.args.get("hide_owned") or "") in ("1", "true", "yes")
        # Preferred original-languages (multi) for GENERAL/curated rails — so the feeds
        # aren't flooded with foreign titles (e.g. Bollywood in Popular/Trending). A rail
        # with an explicit `lang` (a dedicated foreign rail) bypasses this. Default 'en'.
        prefer_langs = None
        if not lang:
            try:
                from . import get_video_db
                raw = get_video_db().get_setting("discover_languages", "en") or "en"
                prefer_langs = {c.strip().lower() for c in raw.split(",") if c.strip()} or None
            except Exception:
                prefer_langs = {"en"}

        def fetch(p):
            if key == "trending":
                return eng.trending()
            if key:
                return eng.discover_curated(key, page=p)
            return eng.discover_filter(kind, genre=genre, year=year, decade=decade,
                                       providers=providers, sort_by=sort, page=p, language=lang)

        try:
            items, seen = [], set()
            # When filtering (hide-owned or language), page DEEPER and drop items server-side
            # until the rail has ~enough to look full — instead of returning a half-empty rail.
            need_fill = hide_owned or bool(prefer_langs)
            target = 24 if need_fill else 0
            max_pages = 8 if need_fill else pages
            for offset in range(max_pages):
                batch = fetch(page + offset) or []
                for it in batch:
                    dk = (it.get("kind"), it.get("tmdb_id"))
                    if dk in seen:
                        continue
                    seen.add(dk)
                    if hide_owned and it.get("library_id") is not None:
                        continue
                    if prefer_langs:
                        ol = (it.get("original_language") or "").lower()
                        if ol and ol not in prefer_langs:
                            continue   # known foreign language not in your preference
                    items.append(it)
                if key == "trending" or not batch:
                    break        # trending is a fixed list; empty batch = TMDB ran out
                if target and len(items) >= target:
                    break        # enough collected
                if not target and offset + 1 >= pages:
                    break        # no filtering: respect the requested page count
            return jsonify({"items": items, "page": page})
        except Exception:
            logger.exception("discover list failed (key=%s)", key)
            return jsonify({"items": [], "page": page})
