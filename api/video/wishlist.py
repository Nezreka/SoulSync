"""Video wishlist API — the curated 'get this' list (movies + episodes).

Atomic units are movies and episodes; adding a whole show or a season just hands
us the explicit episodes to expand into rows. v1 manages membership + the tabbed
Movies/TV page; the search/download engine that fulfils a wish is a later phase.
Reads/writes only video_library.db via the shared VideoDatabase.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.wishlist")

_KINDS = ("movie", "show")
_SCOPES = ("movie", "show", "season", "episode")


def _server():
    """Active video server_source (stored on rows, informational). None on error."""
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:
        return None


def _annotate_live_state(db, kind, items):
    """Stamp reality onto the page's rows: ``downloading`` (an active download
    row exists for the item) and ``upgrade_from`` (owned below the profile
    cutoff — the invisible 'upgrade watch' finally rendered). Best-effort:
    the wishlist must still load if the quality/downloads side hiccups."""
    try:
        from core.automation.handlers.video_process_wishlist import active_download_keys
        from core.video.quality_eval import resolution_rank
        keys = active_download_keys(db.get_active_video_downloads())
        owned = db.wishlist_owned_media_resolutions()
        try:
            from core.video.quality_profile import load as load_profile
            cutoff = resolution_rank((load_profile(db) or {}).get("cutoff_resolution"))
        except Exception:
            cutoff = 0

        def best_res(csv):
            rs = [x.strip() for x in str(csv or "").split(",") if x.strip()]
            if not rs:
                return 0, None
            top = max(rs, key=resolution_rank)
            return resolution_rank(top), top

        if kind == "movie":
            for it in items:
                if ("movie", str(it.get("tmdb_id"))) in keys:
                    it["downloading"] = True
                rank, label = best_res(owned.get("movie:%s" % it.get("tmdb_id")))
                # below cutoff (or no cutoff = always chasing) → live upgrade watch
                if rank and (not cutoff or rank < cutoff):
                    it["upgrade_from"] = label
        else:
            for show in items:
                dl = up = 0
                for season in show.get("seasons") or []:
                    sn = season.get("season_number")
                    for ep in season.get("episodes") or []:
                        en = ep.get("episode_number")
                        if ("episode", str(show.get("tmdb_id")), int(sn or 0), int(en or 0)) in keys:
                            ep["downloading"] = True
                            dl += 1
                        rank, label = best_res(owned.get("ep:%s:%s:%s" % (show.get("tmdb_id"), sn, en)))
                        if rank and (not cutoff or rank < cutoff):
                            ep["upgrade_from"] = label
                            up += 1
                show["downloading_count"] = dl
                show["upgrade_count"] = up
    except Exception:   # noqa: BLE001
        logger.exception("wishlist live-state annotation failed")


def register_routes(bp):
    @bp.route("/wishlist", methods=["GET"])
    def video_wishlist_list():
        """Paged slice for a tab (kind='movie'|'show'), or counts-only with no kind.
        Shows are grouped show→season→episode with wanted/done roll-ups."""
        from . import get_video_db
        try:
            db = get_video_db()
            counts = db.wishlist_counts()
            kind = request.args.get("kind")
            if kind in _KINDS:
                res = db.query_wishlist(
                    kind, search=request.args.get("search", ""), sort=request.args.get("sort", "added"),
                    page=request.args.get("page", 1), limit=request.args.get("limit", 60))
                _annotate_live_state(db, kind, res.get("items") or [])
                return jsonify({"success": True, "kind": kind, "counts": counts, **res})
            return jsonify({"success": True, "counts": counts})
        except Exception:
            logger.exception("Failed to list video wishlist")
            return jsonify({"success": False, "error": "Failed to load wishlist"}), 500

    @bp.route("/wishlist/counts", methods=["GET"])
    def video_wishlist_counts():
        from . import get_video_db
        try:
            db = get_video_db()
            counts = db.wishlist_counts()                 # {movie, show, episode, total(movie+ep)}
            yt = db.youtube_wishlist_counts()             # {channel, video}  (its own table-shape)
            counts["video"] = yt.get("video", 0)
            counts["channel"] = yt.get("channel", 0)
            # The header/sidebar badge is the WHOLE wishlist — movies + episodes + YouTube
            # videos — so fold the YouTube count into the total (it was movies+episodes only,
            # which is why a YouTube-only wishlist showed no badge).
            counts["total"] = counts.get("total", 0) + counts["video"]
            return jsonify({"success": True, **counts})
        except Exception:
            logger.exception("Failed to count video wishlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/wishlist/search", methods=["POST"])
    def video_wishlist_search_now():
        """User-initiated 'Search now' for ONE wished item (or a season/show of
        episodes) — bypasses the release-window gate (the click is the override,
        like Sonarr's manual search) but keeps upgrade-until-cutoff semantics.
        Non-blocking: the search runs in the background; the downloads page /
        badge shows what it grabs. Body: {scope, tmdb_id, season_number?,
        episode_number?}."""
        try:
            data = request.get_json(silent=True) or {}
            scope = str(data.get("scope") or "").lower()
            tmdb_id = data.get("tmdb_id")
            if scope not in _SCOPES or not tmdb_id:
                return jsonify({"success": False, "error": "scope + tmdb_id required"}), 400
            from core.video.wishlist_search import manual_search
            res = manual_search(scope, tmdb_id,
                                season_number=data.get("season_number"),
                                episode_number=data.get("episode_number"))
            return jsonify({"success": True, **res})
        except Exception:
            logger.exception("wishlist manual search failed")
            return jsonify({"success": False, "error": "Search failed to start"}), 500

    @bp.route("/wishlist/search-all", methods=["POST"])
    def video_wishlist_search_all():
        """Search every eligible wished item NOW instead of waiting for the
        hourly drain tick. Gates stay intact (no hunting unreleased films);
        overlap with a running drain is refused per kind ('busy')."""
        try:
            from core.video.wishlist_search import search_all
            return jsonify({"success": True, "kinds": search_all()})
        except Exception:
            logger.exception("wishlist search-all failed")
            return jsonify({"success": False, "error": "Search failed to start"}), 500

    @bp.route("/wishlist/add", methods=["POST"])
    def video_wishlist_add():
        """Add a movie or a set of a show's episodes. Body is one of:
            {"movie": {tmdb_id, title, year?, poster_url?, library_id?}}
            {"show": {tmdb_id, title, poster_url?, library_id?},
             "episodes": [{season_number, episode_number, title?, air_date?}, …]}"""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        srv = _server()
        db = get_video_db()
        try:
            movie = body.get("movie")
            if movie and movie.get("tmdb_id") and (movie.get("title") or "").strip():
                ok = db.add_movie_to_wishlist(
                    int(movie["tmdb_id"]), movie["title"].strip(), year=movie.get("year"),
                    poster_url=movie.get("poster_url") or None,
                    library_id=movie.get("library_id") or None, server_source=srv)
                return jsonify({"success": ok, "added": 1 if ok else 0, "counts": db.wishlist_counts()})

            show = body.get("show")
            episodes = body.get("episodes") or []
            if show and show.get("tmdb_id") and (show.get("title") or "").strip() and episodes:
                n = db.add_episodes_to_wishlist(
                    int(show["tmdb_id"]), show["title"].strip(), episodes,
                    poster_url=show.get("poster_url") or None,
                    library_id=show.get("library_id") or None, server_source=srv)
                return jsonify({"success": n > 0, "added": n, "counts": db.wishlist_counts()})

            return jsonify({"success": False, "error": "movie or show+episodes required"}), 400
        except Exception:
            logger.exception("Failed to add to video wishlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/wishlist/remove", methods=["POST"])
    def video_wishlist_remove():
        """Remove at any granularity. Body: {scope, tmdb_id, season_number?, episode_number?}
        where scope ∈ movie|show|season|episode."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        scope, tmdb_id = body.get("scope"), body.get("tmdb_id")
        if scope not in _SCOPES or not tmdb_id:
            return jsonify({"success": False, "error": "scope and tmdb_id are required"}), 400
        try:
            db = get_video_db()
            removed = db.remove_from_wishlist(
                scope, tmdb_id=int(tmdb_id),
                season_number=body.get("season_number"), episode_number=body.get("episode_number"))
            return jsonify({"success": True, "removed": removed, "counts": db.wishlist_counts()})
        except Exception:
            logger.exception("Failed to remove from video wishlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/wishlist/clear", methods=["POST"])
    def video_wishlist_clear():
        """Empty an entire wishlist tab. Body: {kind} where kind ∈ movie|show|youtube."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in ("movie", "show", "youtube"):
            return jsonify({"success": False, "error": "kind must be movie|show|youtube"}), 400
        try:
            db = get_video_db()
            removed = db.clear_wishlist(kind)
            return jsonify({"success": True, "removed": removed,
                            "counts": db.wishlist_counts(), "youtube_counts": db.youtube_wishlist_counts()})
        except Exception:
            logger.exception("Failed to clear video wishlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/wishlist/backfill-art", methods=["POST"])
    def video_wishlist_backfill_art():
        """Fill episode stills + season posters for rows that predate art-capture.
        One tmdb_season call per (show, season); best-effort. Returns rows filled."""
        from . import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        db = get_video_db()
        eng = get_video_enrichment_engine()
        updated = 0
        try:
            for grp in db.wishlist_art_backfill_targets():
                try:
                    se = eng.tmdb_season(grp["tmdb_id"], grp["season_number"]) or {}
                except Exception:
                    continue
                if se.get("poster_url"):
                    updated += db.set_wishlist_season_poster(grp["tmdb_id"], grp["season_number"], se["poster_url"])
                for ep in (se.get("episodes") or []):
                    en = ep.get("episode_number")
                    if en is None:
                        continue
                    if ep.get("still_url") and db.set_wishlist_still(grp["tmdb_id"], grp["season_number"], en, ep["still_url"]):
                        updated += 1
                    if ep.get("overview"):
                        db.set_wishlist_episode_overview(grp["tmdb_id"], grp["season_number"], en, ep["overview"])
            return jsonify({"success": True, "updated": updated})
        except Exception:
            logger.exception("wishlist art backfill failed")
            return jsonify({"success": False, "updated": updated})

    @bp.route("/wishlist/backfill-movie-art", methods=["POST"])
    def video_wishlist_backfill_movie_art():
        """Fill posters for movie wishlist rows added while upcoming (no art yet). One cached
        tmdb_detail call per movie; best-effort. Returns rows filled."""
        from . import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        db = get_video_db()
        eng = get_video_enrichment_engine()
        updated = 0
        try:
            for row in db.wishlist_movies_missing_art():
                try:
                    d = eng.tmdb_detail("movie", row["tmdb_id"]) or {}
                except Exception:   # noqa: BLE001 - one bad lookup shouldn't sink the batch
                    continue
                if d.get("redirect"):   # now owned → it'll drop off the wishlist on its own
                    continue
                if (d.get("poster_url") or d.get("year")) and db.set_wishlist_movie_art(
                        row["tmdb_id"], poster_url=d.get("poster_url"), year=d.get("year")):
                    updated += 1
            return jsonify({"success": True, "updated": updated})
        except Exception:
            logger.exception("wishlist movie art backfill failed")
            return jsonify({"success": False, "updated": updated})

    @bp.route("/wishlist/check", methods=["POST"])
    def video_wishlist_check():
        """Hydrate cards/modal. Body: {movie_ids: [...], show_tmdb_id?} →
        {movies: [ids already wished], episodes: ['S_E' already wished]}."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        try:
            db = get_video_db()
            st = db.wishlist_state(
                movie_ids=body.get("movie_ids") or [], show_tmdb_id=body.get("show_tmdb_id"))
            out = {"success": True, "movies": sorted(st["movies"]), "episodes": sorted(st["episodes"])}
            shows = body.get("shows")   # multi-show membership for the calendar button
            if shows:
                keys = db.wishlist_keys_for_shows(shows)
                out["by_show"] = {str(tid): sorted(ks) for tid, ks in keys.items()}
            return jsonify(out)
        except Exception:
            logger.exception("Failed to check video wishlist")
            return jsonify({"success": False, "error": "Failed"}), 500
