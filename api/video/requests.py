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


def register_routes(bp):
    @bp.route("/requests", methods=["POST"])
    def video_request_create():
        """File a request: {kind, tmdb_id, title, year?, poster_url?, note?,
        monitor?}. Idempotent per (profile, kind, tmdb) while pending."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        title = (body.get("title") or "").strip()
        if kind not in _KINDS or not tmdb_id or not title:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        monitor = str(body.get("monitor") or "future").lower()
        from core.video.monitor_policy import POLICIES
        if monitor not in POLICIES:
            monitor = "future"
        rid, created = get_video_db().add_video_request(
            profile_id=_me(), requester_name=getattr(g, "profile_name", None),
            kind=kind, tmdb_id=int(tmdb_id), title=title, year=body.get("year"),
            poster_url=body.get("poster_url"), note=(body.get("note") or "")[:500] or None,
            monitor=monitor)
        if rid is None:
            return jsonify({"success": False, "error": "Could not file the request."}), 500
        return jsonify({"success": True, "id": rid, "already": not created})

    @bp.route("/requests", methods=["GET"])
    def video_request_list():
        """Admins see everyone's; members see their own. ?status= filters."""
        from . import get_video_db
        db = get_video_db()
        status = request.args.get("status") or None
        rows = db.list_video_requests(
            profile_id=None if _is_admin() else _me(), status=status)
        return jsonify({"success": True, "requests": rows,
                        "pending": db.video_requests_pending_count(
                            None if _is_admin() else _me())})

    @bp.route("/requests/counts", methods=["GET"])
    def video_request_counts():
        """The nav badge: pending requests (all for admins, own for members)."""
        from . import get_video_db
        return jsonify({"success": True, "pending": get_video_db().video_requests_pending_count(
            None if _is_admin() else _me())})

    @bp.route("/requests/<int:request_id>/approve", methods=["POST"])
    def video_request_approve(request_id):
        """Admin approves → the title enters acquisition: movie → wishlist,
        show → watchlist + the request's monitor policy expanded (P2).
        Fulfillment is atomic-enough: the request only flips to approved after
        the wishlist/watchlist write succeeded."""
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
        wished = 0
        if req["kind"] == "movie":
            ok = db.add_movie_to_wishlist(req["tmdb_id"], req["title"],
                                          year=req.get("year"),
                                          poster_url=req.get("poster_url"))
        else:
            ok = db.add_to_watchlist("show", req["tmdb_id"], req["title"],
                                     poster_url=req.get("poster_url"))
            monitor = str(req.get("monitor") or "future").lower()
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
            return jsonify({"success": False, "error": "Could not add the title — request left pending."}), 500
        db.resolve_video_request(request_id, status="approved", resolved_by=_me(),
                                 admin_response=(body.get("response") or "")[:500] or None)
        return jsonify({"success": True, "wished": wished})

    @bp.route("/requests/<int:request_id>/deny", methods=["POST"])
    def video_request_deny(request_id):
        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        body = request.get_json(silent=True) or {}
        ok = get_video_db().resolve_video_request(
            request_id, status="denied", resolved_by=_me(),
            admin_response=(body.get("response") or "")[:500] or None)
        if not ok:
            return jsonify({"success": False, "error": "Unknown or already-resolved request."}), 404
        return jsonify({"success": True})

    @bp.route("/requests/<int:request_id>", methods=["DELETE"])
    def video_request_withdraw(request_id):
        """A member withdraws their OWN pending request (admins can too)."""
        from . import get_video_db
        db = get_video_db()
        if _is_admin():
            req = db.get_video_request(request_id)
            if req and req["status"] == "pending":
                ok = db.delete_video_request(request_id, req["profile_id"])
            else:
                ok = False
        else:
            ok = db.delete_video_request(request_id, _me())
        if not ok:
            return jsonify({"success": False, "error": "Not yours, or not pending."}), 404
        return jsonify({"success": True})
