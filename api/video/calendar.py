"""Video Calendar — upcoming TV episodes and wishlisted movie releases.

GET  /api/video/calendar?start=&days=&scope=&specials=   the window, with state
GET  /api/video/calendar/followed?start=&days=           followed shows you don't own
POST /api/video/calendar/wishlist-missing               wish the window's missing ones
GET  /api/video/calendar/feed-url  (POST to rotate)      this profile's iCal url
GET  /api/video/calendar/feed/<token>.ics                the feed calendar apps fetch
GET  /api/video/calendar.ics                             the same, for a signed-in session

Isolated: reads video_library.db via VideoDatabase, never imports the music side.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from flask import Response, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video.calendar")

# a month view draws six weeks
MAX_DAYS = 42


def _profile() -> int:
    """The requesting profile: the video watchlist/wishlist are per-profile
    (like the music side) — a non-admin must never see the admin's lists."""
    try:
        from core.profile_context import get_current_profile_id
        return int(get_current_profile_id() or 1)
    except Exception:
        return 1


def _window():
    """(today, start, end, days) from ?start= and ?days=, bounded."""
    days = request.args.get("days", default=7, type=int) or 7
    days = max(1, min(days, MAX_DAYS))
    today = date.today()
    start = today
    start_s = request.args.get("start")
    if start_s:
        try:
            start = datetime.strptime(start_s, "%Y-%m-%d").date()
        except ValueError:
            start = today
    if abs((start - today).days) > 400:     # sane bound around today
        start = today
    return today, start, start + timedelta(days=days - 1), days


def _flag(name, default=False):
    v = request.args.get(name)
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def _episodes(db, start, end, *, scope, specials, profile_id, cap):
    """The window's episodes for one profile, specials and the kids cap applied."""
    from core.video.sources import resolve_video_server
    from .kids import filter_library_items
    eps = db.calendar_upcoming(start.isoformat(), end.isoformat(),
                               server_source=resolve_video_server(),
                               watchlist_only=(scope != "all"), profile_id=profile_id)
    if not specials:
        # season 0 is bonus material; a daily show's specials alone can fill a week
        eps = [e for e in eps if int(e.get("season_number") or 0) != 0]
    if cap is not None:
        eps = filter_library_items(db, eps, cap, "show", id_key="show_id")
    from core.video.calendar_state import collapse_copies
    return collapse_copies(eps)


def _annotate(db, eps, movies, today):
    """Stamp state on every row: owned, downloading (with its percent), queued,
    failed, wanted, ignored, unaired, missing."""
    from core.video import calendar_state as cs
    try:
        live = cs.live_index(db.get_active_video_downloads() or [])
    except Exception:   # noqa: BLE001 - a card without live state still draws
        logger.exception("calendar: active downloads unreadable")
        live = {}
    for e in eps:
        e["acq"] = cs.acquisition_state(e, today=today.isoformat(), active=live)
        e["needs_action"] = cs.needs_action(e["acq"])
        rec = cs.live_for(e, live) if e["acq"] in (cs.DOWNLOADING, cs.QUEUED) else None
        e["progress"] = rec.get("progress") if rec else None
        e["eta_seconds"] = rec.get("eta_seconds") if rec else None
    for m in movies:
        m["acq"] = cs.movie_state(m, active=live)
        rec = live.get(("movie", str(m.get("tmdb_id"))))
        m["progress"] = rec.get("progress") if isinstance(rec, dict) else None


def _followed_rows(db, pid, start, end, today, *, specials):
    """The window's episodes for followed shows that aren't in the library,
    from TMDB, each with its state. Shared by the lane the page draws and the
    bulk add, so the two can't disagree about what's missing."""
    from core.video.sources import resolve_video_server
    from core.video import calendar_state as cs
    from .kids import filter_tmdb_items, video_cap
    follows = [r for r in (db.followed_shows(profile_id=pid,
                                             server_source=resolve_video_server()) or [])
               if r.get("library_id") is None and r.get("tmdb_id")]
    cap = video_cap()
    if cap is not None:
        follows = filter_tmdb_items(db, [{**f, "kind": "show"} for f in follows], cap)
    if not follows:
        return []

    from core.video.enrichment.engine import get_video_enrichment_engine
    from core.video.monitor_policy import episodes_airing_between
    engine = get_video_enrichment_engine()

    def one(f):
        try:
            return f, episodes_airing_between(engine, f["tmdb_id"], start.isoformat(), end.isoformat())
        except Exception:   # noqa: BLE001 - one show can't sink the lane
            return f, {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, follows[:60]))

    wished = db.wishlist_episode_statuses(pid, [f["tmdb_id"] for f in follows])
    try:
        live = cs.live_index(db.get_active_video_downloads() or [])
    except Exception:   # noqa: BLE001
        live = {}
    out = []
    for f, res in results:
        for ep in (res or {}).get("episodes") or []:
            sn, en = ep.get("season_number"), ep.get("episode_number")
            if sn is None or en is None or (not specials and int(sn) == 0):
                continue
            row = {"id": "tmdb-%s-%s-%s" % (f["tmdb_id"], sn, en),
                   "show_id": None, "show_tmdb_id": f["tmdb_id"],
                   "show_title": f.get("title"), "season_number": sn,
                   "episode_number": en, "title": ep.get("title"),
                   "overview": ep.get("overview"), "air_date": ep.get("air_date"),
                   "still_url": ep.get("still_url"),
                   "poster_url": (res or {}).get("poster_url"),
                   "has_file": 0, "monitored": 1, "not_in_library": True,
                   "wishlist_status": wished.get((int(f["tmdb_id"]), int(sn), int(en)))}
            row["acq"] = cs.acquisition_state(row, today=today.isoformat(), active=live)
            row["needs_action"] = cs.needs_action(row["acq"])
            out.append(row)
    out.sort(key=lambda r: (str(r.get("air_date") or ""), str(r.get("show_title") or "")))
    return out


def register_routes(bp):
    @bp.route("/calendar", methods=["GET"])
    def video_calendar():
        from . import get_video_db
        try:
            today, start, end, days = _window()
            db = get_video_db()

            # One-time backfill: existing shows matched TVDB before air time was
            # captured, so re-queue them once (background, only those missing it).
            try:
                if (db.get_setting("airs_time_backfill") or "") != "1":
                    n = db.requeue_shows_for_airtime()
                    db.set_setting("airs_time_backfill", "1")
                    if n:
                        logger.info("calendar: queued %d shows for TVDB air-time backfill", n)
            except Exception:
                logger.exception("airs_time backfill queue failed")

            # scope: 'watchlist' (default — shows you follow/track) or 'all' (every
            # airing show in the library).
            scope = "all" if (request.args.get("scope") or "").lower() == "all" else "watchlist"
            specials = _flag("specials")
            from .kids import filter_tmdb_items, video_cap
            cap = video_cap()
            eps = _episodes(db, start, end, scope=scope, specials=specials,
                            profile_id=_profile(), cap=cap)
            # Movie release events (wishlisted movies): cinema + home-availability
            # dates in the same window.
            movies = db.calendar_movie_releases(start.isoformat(), end.isoformat(),
                                                profile_id=_profile())
            if cap is not None:
                movies = filter_tmdb_items(db, [{**m, "kind": "movie"} for m in movies], cap)
            _annotate(db, eps, movies, today)

            from core.video import calendar_state as cs
            from core.automation.system_status import system_automation_status
            counts: dict[str, int] = {}
            for e in eps:
                counts[e["air_date"]] = counts.get(e["air_date"], 0) + 1
            auto = system_automation_status(("video_add_airing_episodes",
                                             "video_refresh_airing_schedules"))
            return jsonify({
                "today": today.isoformat(),       # real today (for the highlight)
                "start": start.isoformat(),       # window start (may be a future week)
                "end": end.isoformat(),
                "days": days,
                "scope": scope,
                "specials": specials,
                "counts_by_date": counts,
                "total": len(eps),
                "owned": sum(1 for e in eps if e.get("has_file")),
                "acq_counts": cs.summarize(e.get("acq") for e in eps),
                "needs_action": sum(1 for e in eps if e.get("needs_action")),
                "schedule": db.calendar_schedule_freshness(),
                "automations": {"airing": auto.get("video_add_airing_episodes"),
                                "refresh": auto.get("video_refresh_airing_schedules")},
                "episodes": eps,
                "movies": movies,
            })
        except Exception:
            logger.exception("video calendar failed")
            return jsonify({"error": "calendar failed"}), 500

    @bp.route("/calendar/followed", methods=["GET"])
    def video_calendar_followed():
        """Episodes in the window for shows the profile follows but doesn't own.

        The main window is built off the episodes table, which only holds library
        shows, so a followed show you don't own yet was invisible here even though
        the airing automation wishes its episodes. Asked of TMDB (engine-cached),
        so it is its own request and the grid never waits on it."""
        from . import get_video_db
        try:
            today, start, end, _days = _window()
            out = _followed_rows(get_video_db(), _profile(), start, end, today,
                                 specials=_flag("specials"))
            return jsonify({"episodes": out})
        except Exception:
            logger.exception("video calendar followed lane failed")
            return jsonify({"error": "followed lane failed", "episodes": []}), 500

    @bp.route("/calendar/wishlist-missing", methods=["POST"])
    def video_calendar_wishlist_missing():
        """Wish for every aired, monitored, unowned episode in the window that
        nothing is looking for. Same rows the page counts as Missing, decided
        here so the button and the count can't disagree. Each one gets an
        airing receipt, so the automation doesn't offer it a second time."""
        from . import get_video_db
        try:
            body = request.get_json(silent=True) or {}
            today = date.today()
            try:
                start = datetime.strptime(str(body.get("start") or ""), "%Y-%m-%d").date()
                end = datetime.strptime(str(body.get("end") or ""), "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"success": False, "error": "start and end are required"}), 400
            if end < start or (end - start).days >= MAX_DAYS:
                return jsonify({"success": False, "error": "bad window"}), 400
            end = min(end, today)                 # only aired episodes can be missing
            if end < start:
                return jsonify({"success": True, "added": 0, "shows": 0})
            db = get_video_db()
            pid = _profile()
            from .kids import video_cap
            eps = _episodes(db, start, end,
                            scope="all" if body.get("scope") == "all" else "watchlist",
                            specials=bool(body.get("specials")), profile_id=pid, cap=video_cap())
            _annotate(db, eps, [], today)
            from core.video import calendar_state as cs
            from core.video.sources import resolve_video_server
            # followed shows you don't own are missing the same way; the page
            # counts them, so the button adds them
            if body.get("followed", True):
                try:
                    eps = eps + _followed_rows(db, pid, start, end, today,
                                               specials=bool(body.get("specials")))
                except Exception:   # noqa: BLE001 - the library ones still land
                    logger.exception("calendar wishlist-missing: followed lane failed")
            groups: dict = {}
            for e in eps:
                if e.get("acq") != cs.MISSING or not e.get("show_tmdb_id"):
                    continue
                g = groups.setdefault(e["show_tmdb_id"], {"e": e, "eps": []})
                g["eps"].append({"season_number": e["season_number"],
                                 "episode_number": e["episode_number"],
                                 "title": e.get("title"), "air_date": e.get("air_date"),
                                 "overview": e.get("overview"), "still_url": e.get("still_url")})
            added = 0
            server = resolve_video_server()
            for tid, g in groups.items():
                e = g["e"]
                # a show you don't own has no poster proxy; TMDB's poster keeps the
                # wishlist row from reading as unmatched
                poster = (e.get("poster_url") if e.get("not_in_library") else
                          ("/api/video/poster/show/%s" % e["show_id"]) if e.get("show_has_poster") else None)
                added += int(db.add_episodes_to_wishlist(
                    tid, e.get("show_title"), g["eps"], poster_url=poster,
                    library_id=e.get("show_id"), server_source=server, profile_id=pid) or 0)
                db.mark_airing_offered(pid, [(tid, x["season_number"], x["episode_number"])
                                             for x in g["eps"]])
            return jsonify({"success": True, "added": added, "shows": len(groups)})
        except Exception:
            logger.exception("calendar wishlist-missing failed")
            return jsonify({"success": False, "error": "could not add them"}), 500

    @bp.route("/calendar/feed-url", methods=["GET", "POST"])
    def video_calendar_feed_url():
        """This profile's feed url. POST makes a new one; the old url stops working."""
        from . import get_video_db
        try:
            token = get_video_db().calendar_feed_token(_profile(), rotate=request.method == "POST")
            return jsonify({"success": True, "path": "/api/video/calendar/feed/%s.ics" % token})
        except Exception:
            logger.exception("calendar feed url failed")
            return jsonify({"success": False, "error": "feed url unavailable"}), 500

    def _feed(profile_id, cap):
        from . import get_video_db
        from core.video.calendar_feed import build_ics
        db = get_video_db()
        days = max(1, min(request.args.get("days", default=14, type=int) or 14, 60))
        scope = "all" if (request.args.get("scope") or "").lower() == "all" else "watchlist"
        start = date.today()
        end = start + timedelta(days=days - 1)
        eps = _episodes(db, start, end, scope=scope, specials=_flag("specials"),
                        profile_id=profile_id, cap=cap)
        movies = []
        if (request.args.get("movies") or "1") != "0":
            movies = db.calendar_movie_releases(start.isoformat(), end.isoformat(),
                                                profile_id=profile_id)
            if cap is not None:
                # the movie lane is held to the same cap as the episodes
                from .kids import filter_tmdb_items
                movies = filter_tmdb_items(db, [{**m, "kind": "movie"} for m in movies], cap)
        return Response(build_ics(eps, movies), mimetype="text/calendar",
                        headers={"Content-Disposition": "inline; filename=soulsync.ics"})

    @bp.route("/calendar/feed/<token>.ics", methods=["GET"])
    def video_calendar_feed(token):
        """The tokened feed. No session here: the token says whose calendar it
        is, and that profile's own access and rating cap apply."""
        from . import get_video_db
        try:
            from core.video.calendar_feed import profile_may_read_feed
            from core.content_filter import restrictions_for
            pid = get_video_db().calendar_feed_profile(token)
            if pid is None:
                return jsonify({"error": "unknown feed"}), 404
            try:
                from core.content_filter import _load_profile
                profile = _load_profile(pid)
            except Exception:   # noqa: BLE001 - can't vouch for the profile: no feed
                logger.exception("calendar feed: profile %s unreadable", pid)
                return jsonify({"error": "feed unavailable"}), 503
            if not profile_may_read_feed(profile):
                return jsonify({"error": "unknown feed"}), 404
            return _feed(pid, restrictions_for(profile).get("cap"))
        except Exception:
            logger.exception("video calendar feed failed")
            return jsonify({"error": "calendar feed failed"}), 500

    @bp.route("/calendar.ics", methods=["GET"])
    def video_calendar_ics():
        """The feed for a signed-in session (a browser download). Calendar apps
        use the tokened url above."""
        try:
            from .kids import video_cap
            return _feed(_profile(), video_cap())
        except Exception:
            logger.exception("video calendar.ics failed")
            return jsonify({"error": "calendar feed failed"}), 500
