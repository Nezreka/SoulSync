"""Video requests API — the in-app Overseerr (arr-parity P4).

Members (profiles without download rights — or anyone who'd rather ask) file a
request for a movie, a whole show, a single episode, or a YouTube video;
admins approve or deny. Approval IS acquisition: movies land on the wishlist,
shows join the watchlist with the request's monitor policy expanded (P2),
single episodes are wished directly, and YouTube videos join the YouTube
wishlist — the drain/RSS take it from there.

Permissions (#2166, #2168) come from the effective map through access.py:
filing needs that kind's access and a mode above "can't request"; the
quality goes through resolve_request_quality (only what the person may pick
for that kind, the default when they don't, a refusal for anything else);
"download right away" members may ask for a straight get (``get: true``),
which lands like an approval unless the quality needs an OK first.
members see their own asks (everyone's with requests.see_all); moderators
(requests.moderate) approve and decline anyone's but their own; a member can
withdraw their own pending request.
"""

from __future__ import annotations

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.requests")

_KINDS = ("movie", "show", "episode", "youtube")


def _me():
    return int(getattr(g, "profile_id", 1) or 1)


def _is_admin():
    return bool(getattr(g, "is_admin", _me() == 1))


def _kind_ok(kind) -> bool:
    """may this profile see requests of this kind (a tv-only profile never
    sees a movie ask, even with see-everyone's on)?"""
    from . import access
    if kind == "youtube":
        return access.any_side()
    return access.has_kind(kind)


def _server():
    """Active video server_source (stored on rows, informational). None on error."""
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:
        return None


def _quota(kind):
    """{limit, days} for this profile's limit on ``kind`` (movies, or tv for
    a show, episode or youtube video), None when it isn't limited. a member
    whose limit can't be read raises, and the caller refuses the ask rather
    than wave it through uncounted."""
    from . import access
    return access.quota_row(_me(), kind)


def _used(db, quota, kind) -> int:
    """asks spent toward ``kind``'s limit in the quota's window (each kind
    counts on its own, core/requests/asks.py). a count that can't be read is
    a full quota: refuse the ask rather than let it through uncounted."""
    from core.requests.asks import asks_used

    from . import access
    perms = access.ctx().get("perms")
    used = asks_used(_me(), quota["days"], access.limit_kind(kind),
                     perms=perms if isinstance(perms, dict) else None, video_db=db)
    return quota["limit"] if used is None else int(used)


def _quota_states(db) -> dict:
    """{"movies": state, "tv": state} for the kinds this profile can ask for
    that are limited (state: {kind, limit, days, used, remaining})."""
    from core.requests.quota import quota_state

    from . import access
    out = {}
    for kind, probe in (("movies", "movie"), ("tv", "show")):
        if not access.has_kind(probe):
            continue
        quota = _quota(probe)
        if quota:
            out[kind] = dict(quota_state(quota, _used(db, quota, probe)), kind=kind)
    return out


def _tightest(states: dict):
    """the one quota line an older caller reads: the kind with the fewest
    asks left."""
    return min(states.values(), key=lambda q: q["remaining"]) if states else None


def _already_pending(db, kind, tmdb_id, season, episode, youtube_id) -> bool:
    """Has this profile already a pending request for this exact title?
    (quota bypass for re-asks — the add is idempotent anyway)."""
    rows = db.list_video_requests(profile_id=_me(), status="pending")
    for r in rows:
        if r.get("kind") != kind:
            continue
        if kind == "episode":
            if (int(r.get("tmdb_id") or 0) == tmdb_id
                    and int(r.get("season_number") or 0) == season
                    and int(r.get("episode_number") or 0) == episode):
                return True
        elif kind == "youtube":
            if str(r.get("youtube_id") or "") == str(youtube_id):
                return True
        elif int(r.get("tmdb_id") or 0) == tmdb_id:
            return True
    return False


def _title_taken(db, kind, tmdb_id) -> tuple:
    """(on the way, someone else already asked) for a movie or show, from the
    same status the posters show (core/video/title_status.py)."""
    from datetime import date

    from core.video import title_status as ts
    try:
        store = ts.TitleStatusStore(lambda: db._get_connection())
        got = ts.batch(db, store, kind, [tmdb_id], server=_server(),
                       today=date.today().isoformat()).get(int(tmdb_id)) or {}
        rows = store.title_requests(kind, [tmdb_id]).get(int(tmdb_id)) or []
    except Exception:  # noqa: BLE001 - a status hiccup never blocks an ask
        logger.debug("title status check failed for %s %s", kind, tmdb_id, exc_info=True)
        return False, False
    if ts.blocks_request(kind, got.get("status"), got.get("state")):
        return True, False
    others = any(r.get("status") == "pending" and r.get("kind") == kind
                 and int(r.get("profile_id") or 0) != _me() for r in rows)
    return False, others


def _claim_key(req) -> tuple:
    """The title identity a claim scopes to (approve-all dedups on it)."""
    if req.get("kind") == "episode":
        return ("episode", req.get("tmdb_id"), req.get("season_number"),
                req.get("episode_number"))
    if req.get("kind") == "youtube":
        return ("youtube", req.get("youtube_id"))
    return (req.get("kind"), req.get("tmdb_id"))


def _claim_scope(req) -> dict:
    """Extra claim_video_requests kwargs for the request's kind."""
    if req.get("kind") == "episode":
        return {"season_number": req.get("season_number"),
                "episode_number": req.get("episode_number")}
    if req.get("kind") == "youtube":
        return {"youtube_id": req.get("youtube_id")}
    return {}


def _quality_profile(value):
    """a named quality profile id that exists, else None (the default)."""
    from . import get_video_db
    try:
        qp = int(value or 0)
    except (TypeError, ValueError):
        return None
    return qp if qp > 0 and get_video_db().get_named_quality_profile(qp) else None


def _requester_context(pid):
    """a claimant's own permissions (a moderator's pick is held to them)."""
    from core.video.quality_access import context_for_profile
    return context_for_profile(pid)


def _approve_quality(db, req, claim_pids, picked):
    """(quality id or None, refusal or None) for an approval. no pick: what
    the request was filed with (already resolved for the requester). an
    admin's pick is theirs to make; a moderator's must sit inside every
    claimant's allowed set (the approval decides, so their "needs my OK"
    is what's being given)."""
    from . import access
    if picked in (None, "", 0, "0"):
        return req.get("quality_profile_id"), None
    if _is_admin():
        return _quality_profile(picked), None
    chosen = None
    for pid in claim_pids:
        d = access.resolve(db, req["kind"], picked, context=_requester_context(pid))
        if d.refused:
            return None, access.quality_refusal(d)
        chosen = d.profile_id
    return chosen, None


def _own_claim(db, req) -> bool:
    """is the caller one of the people waiting on this title?"""
    me = _me()
    for r in db.list_video_requests(status="pending", limit=1000):
        if _claim_key(r) == _claim_key(req) and int(r.get("profile_id") or 0) == me:
            return True
    return False


def _moderation_refusal(db, req):
    """None when the caller may decide this request, else the response."""
    from . import access
    if _is_admin():
        return None
    if not access.is_moderator():
        return jsonify({"success": False, "error": "Admin only."}), 403
    if not _kind_ok(req.get("kind")):
        return access.kind_refusal(req.get("kind"))
    if _own_claim(db, req):
        return jsonify({"success": False, "error": "own_request",
                        "message": "You asked for this one, so someone else decides it."}), 403
    return None


def _land(db, req, claim_pids, body):
    """put an approved (or straight-got) title into acquisition for every
    claimant: movie → wishlist, episode → that episode wished, youtube → the
    youtube wishlist, show → watchlist + the monitor policy expanded.
    returns (ok, wished). raises on a db failure."""
    from core.requests.video import monitor_for_new_request
    wished = 0
    if req["kind"] == "movie":
        ok = True
        for pid in claim_pids:
            ok = db.add_movie_to_wishlist(req["tmdb_id"], req["title"],
                                          year=req.get("year"),
                                          poster_url=req.get("poster_url"),
                                          profile_id=pid) and ok
    elif req["kind"] == "episode":
        # the row title is "Show S02E07" (built by episode_request_title);
        # the wishlist wants the bare show title
        import re as _re
        show_title = _re.sub(r" S\d+E\d+$", "", req["title"] or "")
        ep = {"season_number": int(req["season_number"]),
              "episode_number": int(req["episode_number"])}
        ok = True
        for pid in claim_pids:
            w = db.add_episodes_to_wishlist(
                int(req["tmdb_id"]), show_title or req["title"], [ep],
                poster_url=req.get("poster_url"),
                profile_id=pid)
            wished += w
            # idempotent: 0 new rows means already wished — the wish exists,
            # so the approval still lands (a DB failure leaves no row).
            ok = (w > 0 or db.wishlist_has_episode(
                int(req["tmdb_id"]), int(req["season_number"]),
                int(req["episode_number"]),
                profile_id=pid)) and ok
    elif req["kind"] == "youtube":
        # landed on disk between filing and approval: nothing to wish,
        # the arrival sweep marks it available
        if str(req.get("youtube_id") or "") in (db.owned_youtube_video_ids() or []):
            ok = True
        else:
            # the wishlist groups by channel; a request filed without
            # channel context still resolves — the video stands in as its
            # own channel row rather than failing the approval
            channel = {"youtube_id": req.get("channel_youtube_id") or req.get("youtube_id"),
                       "title": req.get("channel_title") or req["title"]}
            ok = True
            for pid in claim_pids:
                ok = db.add_videos_to_wishlist(
                    channel, [{"youtube_id": req.get("youtube_id"),
                               "title": req["title"],
                               "thumbnail_url": req.get("poster_url")}],
                    server_source=_server(), allow_downloaded=True,
                    profile_id=pid) > 0 and ok
    else:
        ok = True
        for pid in claim_pids:
            ok = db.add_to_watchlist("show", req["tmdb_id"], req["title"],
                                     poster_url=req.get("poster_url"),
                                     profile_id=pid) and ok
        monitor = (monitor_for_new_request("show", body.get("monitor")) if body.get("monitor")
                   else str(req.get("monitor") or "all").lower())
        if ok and monitor != "future":
            try:
                from datetime import date

                from core.video.enrichment.engine import get_video_enrichment_engine
                from core.video.monitor_policy import episodes_for_policy
                eps = episodes_for_policy(get_video_enrichment_engine(),
                                          int(req["tmdb_id"]), monitor,
                                          date.today().isoformat())
                if eps:
                    for pid in claim_pids:
                        wished += db.add_episodes_to_wishlist(
                            int(req["tmdb_id"]), req["title"], eps,
                            poster_url=req.get("poster_url"),
                            profile_id=pid)
            except Exception:   # noqa: BLE001 - expansion is best-effort, approval still lands
                logger.exception("request approve: policy expansion failed for %s", req["tmdb_id"])
    return ok, wished


def _stamp_quality(db, req, claim_pids, qp, *, series=False) -> None:
    """the quality the request asked for (or the approver's pick); youtube
    has no quality profiles; an episode request stamps only its own row. a
    show's pick is also kept on the series (video_title_profiles) so the
    episodes that air later keep it."""
    if not qp or req["kind"] == "youtube":
        return
    keep_on_series = False
    if req["kind"] == "show":
        try:
            keep_on_series = series or not db.quality_profile_id_for("show", tmdb_id=int(req["tmdb_id"]))
        except Exception:  # noqa: BLE001
            keep_on_series = series
    for pid in claim_pids:
        if req["kind"] == "episode":
            db.set_wishlist_quality_for_tmdb(req["tmdb_id"], qp,
                                             season_number=req.get("season_number"),
                                             episode_number=req.get("episode_number"),
                                             profile_id=pid)
        else:
            db.set_wishlist_quality_for_tmdb(req["tmdb_id"], qp,
                                             profile_id=pid)
    if keep_on_series:
        # an approver's own pick is kept on the series; the requester's only
        # where the household hadn't picked one for it yet
        try:
            db.set_title_profile_choice("show", int(req["tmdb_id"]), qp)
        except Exception:  # noqa: BLE001 - the wishlist rows already carry it
            logger.warning("show %s: couldn't keep the quality on the series", req["tmdb_id"],
                           exc_info=True)


def _admin_name():
    """who a member's ask goes to, for the dialog's line ("Sam gets asked
    first"). the first admin's name, else None (the dialog says "an admin")."""
    from core.video.quality_access import first_admin_name
    return first_admin_name()


def _notify(profile_id, message, kind="info"):
    try:
        from core.profile_notify import notify_profile
        notify_profile(profile_id, message, kind, link="video-requests")
    except Exception:  # noqa: BLE001 - a note never fails the action
        logger.debug("request note failed", exc_info=True)


def _tmdb_lookup(kind, tmdb_id):
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().tmdb_detail(kind, tmdb_id)


def sweep_arrivals():
    """approved titles that reached the library: stamp + tell the requester."""
    from core.requests.video import sweep_arrivals as _sweep

    from . import get_video_db
    try:
        return _sweep(get_video_db(), _notify)
    except Exception:  # noqa: BLE001 - the page still loads without the sweep
        logger.exception("video request arrival sweep failed")
        return 0


def file_request(body):
    """File a request (the POST /requests body), or with ``get: true`` and a
    "download right away" profile, get it now. Shared with the wishlist add,
    whose ask-first qualities become requests (#2168)."""
    from core.requests.video import (episode_request_title,
                                     monitor_for_new_request, parse_season_episode,
                                     parse_tmdb_id, parse_youtube_id,
                                     request_metadata, youtube_poster_url)

    from . import access, get_video_db
    body = body if isinstance(body, dict) else {}
    kind = body.get("kind")
    if kind not in _KINDS:
        return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
    db = get_video_db()
    # what this profile may ask for at all (#2166): the kind's own access
    # and a mode above "can't request"; youtube rides on its own row
    if kind == "youtube":
        if not access.can("video.youtube"):
            return access.refusal("video.youtube")
    elif not access.has_kind(kind):
        return access.kind_refusal(kind)
    mode = access.mode(None if kind == "youtube" else kind)
    if mode == "none":
        return access.get_refusal(None if kind == "youtube" else kind)

    season = episode = None
    youtube_id = None
    channel = {}
    if kind == "episode":
        tmdb_id = parse_tmdb_id(body.get("tmdb_id"))
        season = parse_season_episode(body.get("season"))
        episode = parse_season_episode(body.get("episode"))
        if tmdb_id is None or season is None or episode is None:
            return jsonify({"success": False,
                            "error": "episode requests need tmdb_id, season and episode"}), 400
        # the show's title/poster from tmdb, like movies/shows
        meta = request_metadata("show", tmdb_id, body, _tmdb_lookup)
        if not meta["title"]:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        title = episode_request_title(meta["title"], season, episode)
        year, poster_url = meta["year"], meta["poster_url"]
        if db.episode_in_library(tmdb_id, season, episode):
            return jsonify({"success": False, "error": "That's already in the library.",
                            "in_library": True}), 409
        monitor = "future"
    elif kind == "youtube":
        tmdb_id = 0
        youtube_id = parse_youtube_id(body.get("youtube_id"))
        title = str(body.get("title") or "").strip()[:300]
        if not youtube_id or not title:
            return jsonify({"success": False, "error": "youtube requests need youtube_id and title"}), 400
        channel = body.get("channel") or {}
        year, poster_url = None, youtube_poster_url(youtube_id)
        monitor = "future"
        if str(youtube_id) in (db.owned_youtube_video_ids() or []):
            return jsonify({"success": False, "error": "That's already in the library.",
                            "in_library": True}), 409
    else:
        tmdb_id = parse_tmdb_id(body.get("tmdb_id"))
        if tmdb_id is None:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        # title/year/poster come from tmdb, not from what the member typed:
        # the admin approves on them and the indexer searches with them
        meta = request_metadata(kind, tmdb_id, body, _tmdb_lookup)
        if not meta["title"]:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        if meta["owned"] and kind == "movie":
            return jsonify({"success": False, "error": "That's already in the library.",
                            "in_library": True}), 409
        # shows stay requestable when owned: the show may be missing
        # episodes, which is a legitimate ask (the list annotates
        # in_library so the admin sees it)
        monitor = monitor_for_new_request(kind, body.get("monitor"))
        title = meta["title"]
        year, poster_url = meta["year"], meta["poster_url"]
    # a kids profile asks only for what its rating cap allows (the title
    # pages already hide the rest; this is the deep link and the api)
    if kind in ("movie", "show", "episode"):
        from .kids import restricted_response, tmdb_title_allowed, video_cap
        cap = video_cap()
        if cap is not None and not tmdb_title_allowed(
                db, "movie" if kind == "movie" else "show", tmdb_id, cap):
            return restricted_response("movie" if kind == "movie" else "show")
    # the quality (#2168): only what this profile may pick for this kind,
    # its default when it picks none, refused for anything else. an
    # admin's pick is theirs. youtube has no quality profiles.
    quality = None
    needs_ok = mode != "download"
    if kind != "youtube":
        decision = access.resolve(db, kind, body.get("quality_profile_id"))
        if decision.refused:
            return access.quality_refusal(decision)
        quality, needs_ok = decision.profile_id, decision.needs_approval
    # a "download right away" profile asking to get it: it lands now,
    # like an approval, unless the quality needs an OK first (then it is
    # a request like any other, same row, same Requests page)
    straight = bool(body.get("get")) and not needs_ok and kind in ("movie", "show", "episode")
    # already on its way (wishlisted, approved, downloading): a second ask
    # would only be a duplicate in the admin's queue (#2160). someone
    # else's ask still waiting is fine: this one joins it (the queue shows
    # one row, everyone who asked hears the answer) and costs no quota.
    joining = False
    if kind in ("movie", "show"):
        on_way, joining = _title_taken(db, kind, tmdb_id)
        if on_way:
            return jsonify({"success": False, "on_the_way": True,
                            "error": "That's already on the way."}), 409
    if straight:
        req = {"kind": kind, "tmdb_id": tmdb_id, "title": title, "year": year,
               "poster_url": poster_url, "monitor": monitor, "season_number": season,
               "episode_number": episode}
        if kind == "show":
            from core.video.anime_hint import note_show_wanted
            note_show_wanted(get_video_db, tmdb_id)
        try:
            ok, wished = _land(db, req, [_me()], {})
        except Exception:  # noqa: BLE001 - nothing was claimed, so nothing to put back
            logger.exception("straight get failed for %s %s", kind, tmdb_id)
            ok, wished = False, 0
        if not ok:
            return jsonify({"success": False, "error": "Couldn't add it. Try again in a moment."}), 500
        _stamp_quality(db, req, [_me()], quality)
        return jsonify({"success": True, "got": True, "wished": wished, "quality_profile_id": quality})
    # a limited profile's quota (asks per window, any outcome counts)
    # each kind has its own: a movie spends the movie limit, a show, an
    # episode or a youtube video the TV one
    from core.requests.quota import over_quota, quota_message, quota_state
    try:
        quota = _quota(kind)
    except Exception:  # noqa: BLE001 - can't read the limit, so no ask goes through uncounted
        logger.warning("request limit lookup failed for profile %s", _me(), exc_info=True)
        return jsonify({"success": False,
                        "error": "Couldn't check your request limit. Try again in a moment."}), 503
    if quota:
        used = _used(db, quota, kind)
        if over_quota(quota, used) and not joining and not _already_pending(
                db, kind, tmdb_id, season, episode, youtube_id):
            limit_kind = access.limit_kind(kind)
            return jsonify({"success": False, "error": quota_message(quota, limit_kind),
                            "quota": dict(quota_state(quota, used), kind=limit_kind)}), 429
    qp = quality
    rid, created = db.add_video_request(
        profile_id=_me(), requester_name=getattr(g, "profile_name", None),
        kind=kind, tmdb_id=tmdb_id, title=title, year=year,
        poster_url=poster_url, note=(body.get("note") or "")[:500] or None,
        monitor=monitor, quality_profile_id=qp, season_number=season,
        episode_number=episode, youtube_id=youtube_id,
        channel_youtube_id=(str(channel.get("youtube_id") or "").strip() or None),
        channel_title=(str(channel.get("title") or "").strip()[:300] or None))
    if rid is None:
        return jsonify({"success": False, "error": "Could not file the request."}), 500
    if created:
        try:      # 'Request Filed' automation trigger
            from core.video.download_events import publish
            publish("video_request_created", {
                "kind": kind, "title": title,
                "requester": getattr(g, "profile_name", None) or ""})
        except Exception:   # noqa: BLE001 - events never disturb the request
            logger.exception("request-created event publish failed")
    return jsonify({"success": True, "id": rid, "already": not created,
                    "joined": bool(created and joining), "quality_profile_id": qp,
                    "asked": True})


def register_routes(bp):
    @bp.route("/requests", methods=["POST"])
    def video_request_create():
        """File a request. Bodies per kind:
        - movie/show: {kind, tmdb_id, title, year?, poster_url?, note?, monitor?}
        - episode: {kind: 'episode', tmdb_id (the show), season, episode, note?}
        - youtube: {kind: 'youtube', youtube_id, title, channel?, note?}
        Idempotent per identity while pending (episode scopes to the episode,
        youtube to the video id)."""
        return file_request(request.get_json(silent=True) or {})

    @bp.route("/requests/options", methods=["GET"])
    def video_request_options():
        """What the request dialog may offer this profile (#2168), from the
        same rules the server holds every path to. Without ?kind: the mode
        for each kind (the buttons). With ?kind=movie|show: that kind's
        qualities (only the ones this profile may pick, its default first
        picked, "asks" on the ones that need an OK), the quota line, and
        who gets asked. No quality blobs, ever."""
        from . import access, get_video_db
        db = get_video_db()
        modes = {k: (access.mode(k) if access.has_kind(k) else "none") for k in ("movie", "show")}
        youtube = "none"
        if access.can("video.youtube"):
            youtube = access.mode(None)
        out = {"success": True, "modes": dict(modes, youtube=youtube),
               "admin": _is_admin(), "moderator": access.is_moderator()}
        kind = access.title_kind(request.args.get("kind"))
        if kind:
            out["kind"] = kind
            out["mode"] = modes[kind]
            out["qualities"] = access.allowed_qualities(db, kind) if modes[kind] != "none" else []
            from core.requests.quota import quota_state
            try:
                quota = _quota(kind) if not _is_admin() else None
                out["quota"] = (dict(quota_state(quota, _used(db, quota, kind)), kind=access.limit_kind(kind))
                                if quota else None)
            except Exception:  # noqa: BLE001 - the dialog still opens without the quota line
                logger.debug("request limit lookup failed for profile %s", _me(), exc_info=True)
                out["quota"] = None
            out["asks_who"] = _admin_name()
        return jsonify(out)

    @bp.route("/requests", methods=["GET"])
    def video_request_list():
        """Admins see everyone's; members see their own. ?status= filters.
        Rows carry ``in_library`` (the title now exists in the video library)
        so an approved request visibly progresses to 'In library' instead of
        sitting ambiguously forever; ``counts`` feeds the page tabs."""
        from . import access, get_video_db
        sweep_arrivals()
        db = get_video_db()
        status = request.args.get("status") or None
        # everyone's for admins, moderators and requests.see_all; own otherwise
        scope = None if access.may_see_all_requests() else _me()
        rows = db.list_video_requests(profile_id=scope, status=status)
        counts = db.video_requests_status_counts(scope)
        if not _is_admin() and not all(access.has_kind(k) for k in ("movie", "show")):
            # a kind this profile can't see stays out, counts included
            rows = [r for r in rows if _kind_ok(r.get("kind"))]
            seen = [r for r in db.list_video_requests(profile_id=scope, limit=1000)
                    if _kind_ok(r.get("kind"))]
            counts = {k: sum(1 for r in seen if r.get("status") == k)
                      for k in ("pending", "approved", "denied")}
        db.annotate_requests_in_library(rows)
        db.annotate_request_progress(rows)
        moderator = access.is_moderator()
        me = _me()
        for r in rows:
            # who may decide this row: admins any; a moderator anyone's but
            # their own (the page shows Approve / Decline from this)
            r["can_decide"] = bool(r.get("status") == "pending" and (
                _is_admin() or (moderator and int(r.get("profile_id") or 0) != me)))
        try:
            quotas = _quota_states(db) if not _is_admin() else {}
        except Exception:  # noqa: BLE001 - the list still loads without the quota line
            logger.warning("request limit lookup failed for profile %s", _me(), exc_info=True)
            quotas = {}
        return jsonify({"success": True, "requests": rows,
                        "counts": counts, "pending": counts.get("pending", 0),
                        "moderator": moderator, "see_all": scope is None,
                        "quotas": quotas, "quota": _tightest(quotas)})

    @bp.route("/requests/counts", methods=["GET"])
    def video_request_counts():
        """The nav badge: pending requests (all for admins, own for members)."""
        from . import access, get_video_db
        sweep_arrivals()
        return jsonify({"success": True, "pending": get_video_db().video_requests_pending_count(
            None if access.may_see_all_requests() else _me())})

    @bp.route("/requests/<int:request_id>/approve", methods=["POST"])
    def video_request_approve(request_id):
        """Admin approves → the title enters acquisition: movie → wishlist,
        show → watchlist + the monitor policy expanded (the admin may override
        the requester's pick with ``{"monitor": ...}``), episode → that one
        episode wished, youtube → the video joins the YouTube wishlist.
        Every pending request for the same title is approved together, and
        everyone who asked hears about it. The rows are claimed FIRST
        (pending → approved in one statement); if the wishlist/watchlist
        write fails they go back."""
        from . import access, get_video_db
        db = get_video_db()
        if not _is_admin() and not access.is_moderator():
            return jsonify({"success": False, "error": "Admin only."}), 403
        req = db.get_video_request(request_id)
        if not req:
            return jsonify({"success": False, "error": "Unknown request."}), 404
        if req["status"] != "pending":
            return jsonify({"success": False, "error": "Already resolved."}), 409
        refused = _moderation_refusal(db, req)
        if refused:
            return refused

        body = request.get_json(silent=True) or {}
        # the approver's quality pick, checked before anything is claimed
        if body.get("quality_profile_id") not in (None, "", 0, "0") and not _is_admin():
            pending = [int(r.get("profile_id") or 1) for r in db.list_video_requests(status="pending", limit=1000)
                       if _claim_key(r) == _claim_key(req)]
            _q, bad = _approve_quality(db, req, sorted(set(pending)) or [int(req.get("profile_id") or 1)],
                                       body.get("quality_profile_id"))
            if bad:
                return bad
        claimed = db.claim_video_requests(
            req["kind"], req["tmdb_id"], resolved_by=_me(),
            admin_response=(body.get("response") or "")[:500] or None,
            **_claim_scope(req))
        if not claimed:
            return jsonify({"success": False, "error": "Already resolved."}), 409

        wished = 0
        if req["kind"] in ("show", "episode") and req.get("tmdb_id"):
            # the approve sheet may carry the show's type (``series_type``);
            # without one, a show in no library gets TMDB's suggestion. Either
            # way it is stored before the wishes, so the drain files right.
            from core.video.anime_hint import note_show_wanted
            note_show_wanted(get_video_db, req["tmdb_id"],
                             (request.get_json(silent=True) or {}).get("series_type"))
        # per-profile wishlists: every claimant gets the wish on their own
        # list. duplicates across profiles are fine — the download drain
        # dedupes to a single download.
        claim_pids = sorted({int(r.get("profile_id") or 1) for r in claimed})
        try:
            ok, wished = _land(db, req, claim_pids, body)
        except Exception:  # noqa: BLE001 - the claim already landed; put it back
            logger.exception("request approve: acquisition failed for request %s", request_id)
            try:
                db.unclaim_video_requests([r["id"] for r in claimed])
            except Exception:  # noqa: BLE001 - unclaim is best-effort; the failure is already logged
                logger.exception("request approve: unclaim failed for request %s", request_id)
            return jsonify({"success": False, "error": "Could not add the title — request left pending."}), 500
        if not ok:
            db.unclaim_video_requests([r["id"] for r in claimed])
            return jsonify({"success": False, "error": "Could not add the title — request left pending."}), 500
        # the quality the request was filed with (resolved for the requester),
        # or the approver's pick: an admin's as given, a moderator's held to
        # each claimant's allowed set (checked above, before the claim)
        picked = body.get("quality_profile_id") not in (None, "", 0, "0")
        qp, _bad = _approve_quality(db, req, claim_pids, body.get("quality_profile_id"))
        _stamp_quality(db, req, claim_pids, qp, series=picked)
        for r in claimed:
            _notify(r["profile_id"], f"{req['title']} was approved, it's on the way", "success")
        try:      # 'Request Approved' automation trigger
            from core.video.download_events import publish
            publish("video_request_approved", {
                "kind": req["kind"], "title": req["title"],
                "requester": ", ".join(sorted({r.get("requester_name") or "" for r in claimed} - {""}))})
        except Exception:   # noqa: BLE001 - events never disturb the approval
            logger.exception("request-approved event publish failed")
        return jsonify({"success": True, "wished": wished, "kind": req["kind"],
                        "approved": len(claimed)})

    @bp.route("/requests/approve-all", methods=["POST"])
    def video_request_approve_all():
        """Approve every pending title (each with its own requested seasons)."""
        from . import access, get_video_db
        if not _is_admin() and not access.is_moderator():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        seen, approved, failed, skipped = set(), 0, 0, 0
        for r in db.list_video_requests(status="pending"):
            key = _claim_key(r)
            if key in seen:
                continue
            seen.add(key)
            if _moderation_refusal(db, r) is not None:
                skipped += 1      # a moderator's own ask, or a kind they can't see
                continue
            # the single-approve route does the claim + acquisition + notes;
            # the body here is empty, so each title keeps its requested seasons
            resp = video_request_approve(r["id"])
            code = resp[1] if isinstance(resp, tuple) else 200
            if code == 200:
                approved += 1
            else:
                failed += 1
        return jsonify({"success": True, "approved": approved, "failed": failed, "skipped": skipped})

    @bp.route("/requests/<int:request_id>/deny", methods=["POST"])
    def video_request_deny(request_id):
        """Declines the title for everyone who asked for it, with the note."""
        from . import access, get_video_db
        if not _is_admin() and not access.is_moderator():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        req = db.get_video_request(request_id)
        if not req or req["status"] != "pending":
            return jsonify({"success": False, "error": "Unknown or already-resolved request."}), 404
        refused = _moderation_refusal(db, req)
        if refused:
            return refused
        body = request.get_json(silent=True) or {}
        note = (body.get("response") or "")[:500] or None
        claimed = db.claim_video_requests(req["kind"], req["tmdb_id"], resolved_by=_me(),
                                          admin_response=note, status="denied",
                                          **_claim_scope(req))
        if not claimed:
            return jsonify({"success": False, "error": "Unknown or already-resolved request."}), 404
        for r in claimed:
            _notify(r["profile_id"], f"{req['title']} was declined" + (f": {note}" if note else ""),
                    "warning")
        try:      # 'Request Declined' automation trigger
            from core.video.download_events import publish
            publish("video_request_denied", {
                "kind": req["kind"], "title": req["title"], "reason": note or "",
                "requester": ", ".join(sorted({r.get("requester_name") or "" for r in claimed} - {""}))})
        except Exception:   # noqa: BLE001 - events never disturb the decision
            logger.exception("request-denied event publish failed")
        return jsonify({"success": True, "denied": len(claimed)})

    @bp.route("/requests/resolved", methods=["DELETE"])
    def video_request_clear_resolved():
        """Clear request history: removes approved/denied rows (admin: all;
        member: their own). Pending requests are never touched, and approval
        side effects (wishlist/watchlist) stay."""
        from . import get_video_db
        removed = get_video_db().clear_resolved_video_requests(
            None if _is_admin() else _me())
        return jsonify({"success": True, "removed": removed})

    @bp.route("/requests/<int:request_id>", methods=["DELETE"])
    def video_request_withdraw(request_id):
        """Pending: a member withdraws their OWN request (admins any).
        Approved/denied: remove the row from history (same ownership rules) —
        the approval's wishlist/watchlist entry is untouched."""
        from . import get_video_db
        db = get_video_db()
        ok = db.delete_video_request(
            request_id,
            profile_id=None if _is_admin() else _me(),
            include_resolved=True)
        if not ok:
            return jsonify({"success": False, "error": "Not yours, or already gone."}), 404
        return jsonify({"success": True})
