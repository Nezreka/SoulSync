"""Video requests API — the in-app Overseerr (arr-parity P4).

Members (profiles without download rights — or anyone who'd rather ask) file a
request for a movie/show; admins approve or deny. Approval IS acquisition:
movies land on the wishlist, shows join the watchlist with the request's
monitor policy expanded (P2), and the drain/RSS take it from there.

Permissions ride the blueprint's g context: filing/listing is open to any
video-side profile (members see only their own), approve/deny/list-all is
admin-only, and a member can withdraw their own pending request.
"""

from __future__ import annotations

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.requests")

_KINDS = ("movie", "show")


def _me():
    return int(getattr(g, "profile_id", 1) or 1)


def _is_admin():
    return bool(getattr(g, "is_admin", _me() == 1))


def _profile_row(pid):
    """the quota fields web_server's before_request stashed on g (the video
    side never imports the music db)."""
    return {"id": pid, "is_admin": _is_admin(),
            "request_limit": getattr(g, "request_limit", 0),
            "request_limit_days": getattr(g, "request_limit_days", 7)}


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


def register_routes(bp):
    @bp.route("/requests", methods=["POST"])
    def video_request_create():
        """File a request: {kind, tmdb_id, title, year?, poster_url?, note?,
        monitor?}. Idempotent per (profile, kind, tmdb) while pending."""
        from core.requests.video import monitor_for_new_request, parse_tmdb_id, request_metadata

        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = parse_tmdb_id(body.get("tmdb_id"))
        if kind not in _KINDS or tmdb_id is None:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        # title/year/poster come from tmdb, not from what the member typed:
        # the admin approves on them and the indexer searches with them
        meta = request_metadata(kind, tmdb_id, body, _tmdb_lookup)
        if not meta["title"]:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        if meta["owned"] and kind == "movie":
            return jsonify({"success": False, "error": "That's already in the library.",
                            "in_library": True}), 409
        monitor = monitor_for_new_request(kind, body.get("monitor"))
        title = meta["title"]
        # a limited profile's quota (asks per window, any outcome counts)
        from core.requests.quota import over_quota, quota_for, quota_message, quota_state
        quota = quota_for(_profile_row(_me()))
        if quota:
            used = get_video_db().count_video_requests_since(_me(), quota["days"])
            already = any(int(r.get("tmdb_id") or 0) == tmdb_id and r.get("kind") == kind
                          for r in get_video_db().list_video_requests(profile_id=_me(), status="pending"))
            if over_quota(quota, used) and not already:
                return jsonify({"success": False, "error": quota_message(quota),
                                "quota": quota_state(quota, used)}), 429
        rid, created = get_video_db().add_video_request(
            profile_id=_me(), requester_name=getattr(g, "profile_name", None),
            kind=kind, tmdb_id=tmdb_id, title=title, year=meta["year"],
            poster_url=meta["poster_url"], note=(body.get("note") or "")[:500] or None,
            monitor=monitor)
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
        return jsonify({"success": True, "id": rid, "already": not created})

    @bp.route("/requests", methods=["GET"])
    def video_request_list():
        """Admins see everyone's; members see their own. ?status= filters.
        Rows carry ``in_library`` (the title now exists in the video library)
        so an approved request visibly progresses to 'In library' instead of
        sitting ambiguously forever; ``counts`` feeds the page tabs."""
        from . import get_video_db
        sweep_arrivals()
        db = get_video_db()
        status = request.args.get("status") or None
        scope = None if _is_admin() else _me()
        rows = db.list_video_requests(profile_id=scope, status=status)
        db.annotate_requests_in_library(rows)
        counts = db.video_requests_status_counts(scope)
        from core.requests.quota import quota_for, quota_state
        quota = quota_for(_profile_row(_me())) if not _is_admin() else None
        return jsonify({"success": True, "requests": rows,
                        "counts": counts, "pending": counts.get("pending", 0),
                        "quota": quota_state(quota, db.count_video_requests_since(_me(), quota["days"]))
                        if quota else None})

    @bp.route("/requests/counts", methods=["GET"])
    def video_request_counts():
        """The nav badge: pending requests (all for admins, own for members)."""
        from . import get_video_db
        sweep_arrivals()
        return jsonify({"success": True, "pending": get_video_db().video_requests_pending_count(
            None if _is_admin() else _me())})

    @bp.route("/requests/<int:request_id>/approve", methods=["POST"])
    def video_request_approve(request_id):
        """Admin approves → the title enters acquisition: movie → wishlist,
        show → watchlist + the monitor policy expanded (the admin may override
        the requester's pick with ``{"monitor": ...}``). Every pending request
        for the same title is approved together, and everyone who asked hears
        about it. The rows are claimed FIRST (pending → approved in one
        statement); if the wishlist/watchlist write fails they go back."""
        from core.requests.video import monitor_for_new_request

        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        req = db.get_video_request(request_id)
        if not req:
            return jsonify({"success": False, "error": "Unknown request."}), 404
        if req["status"] != "pending":
            return jsonify({"success": False, "error": "Already resolved."}), 409

        body = request.get_json(silent=True) or {}
        claimed = db.claim_video_requests(
            req["kind"], req["tmdb_id"], resolved_by=_me(),
            admin_response=(body.get("response") or "")[:500] or None)
        if not claimed:
            return jsonify({"success": False, "error": "Already resolved."}), 409

        wished = 0
        if req["kind"] == "movie":
            ok = db.add_movie_to_wishlist(req["tmdb_id"], req["title"],
                                          year=req.get("year"),
                                          poster_url=req.get("poster_url"))
        else:
            ok = db.add_to_watchlist("show", req["tmdb_id"], req["title"],
                                     poster_url=req.get("poster_url"))
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
                        wished = db.add_episodes_to_wishlist(
                            int(req["tmdb_id"]), req["title"], eps,
                            poster_url=req.get("poster_url"))
                except Exception:   # noqa: BLE001 - expansion is best-effort, approval still lands
                    logger.exception("request approve: policy expansion failed for %s", req["tmdb_id"])
        if not ok:
            db.unclaim_video_requests([r["id"] for r in claimed])
            return jsonify({"success": False, "error": "Could not add the title — request left pending."}), 500
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
        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        seen, approved, failed = set(), 0, 0
        for r in db.list_video_requests(status="pending"):
            key = (r["kind"], r["tmdb_id"])
            if key in seen:
                continue
            seen.add(key)
            # the single-approve route does the claim + acquisition + notes;
            # the body here is empty, so each title keeps its requested seasons
            resp = video_request_approve(r["id"])
            code = resp[1] if isinstance(resp, tuple) else 200
            if code == 200:
                approved += 1
            else:
                failed += 1
        return jsonify({"success": True, "approved": approved, "failed": failed})

    @bp.route("/requests/<int:request_id>/deny", methods=["POST"])
    def video_request_deny(request_id):
        """Declines the title for everyone who asked for it, with the note."""
        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        req = db.get_video_request(request_id)
        if not req or req["status"] != "pending":
            return jsonify({"success": False, "error": "Unknown or already-resolved request."}), 404
        body = request.get_json(silent=True) or {}
        note = (body.get("response") or "")[:500] or None
        claimed = db.claim_video_requests(req["kind"], req["tmdb_id"], resolved_by=_me(),
                                          admin_response=note, status="denied")
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
