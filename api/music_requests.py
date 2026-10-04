"""music requests: the music side of the in-app overseerr.

a profile without download rights adds to its wishlist like anyone else; the
scheduled run only downloads what an admin approved (core/requests/music.py).
this is the queue an admin works through and the history a member reads.

identity is the session's (core.profile_context), never the request body.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.permissions import profile_can_download
from core.profile_context import get_current_profile_id, is_admin_request
from core.requests.music import describe, fulfillment_status, group_rows
from utils.logging_config import get_logger

logger = get_logger("api.music_requests")

bp = Blueprint("music_requests", __name__)

get_database = None
# injected by web_server via configure(): validates the music videos dir,
# builds root + deps and starts the job, exactly like /api/music-video/download
_start_music_video = None


def configure(*, get_database, start_music_video=None):
    globals()["get_database"] = get_database
    globals()["_start_music_video"] = start_music_video


def create_blueprint():
    return bp


def _event(event_type, group, **extra):
    """music_request_approved / _declined / _available for automations."""
    try:
        from core.app_events import publish
        publish(event_type, {"kind": group.get("kind") or "", "title": group.get("title") or "",
                             "artist": group.get("artist") or "",
                             "requester": group.get("requester_name") or "", **extra})
    except Exception:  # noqa: BLE001 - an automation hook never fails the request
        logger.debug("music request event failed", exc_info=True)


def _notify(profile_id, message, kind="info"):
    try:
        from core.profile_notify import notify_profile
        notify_profile(profile_id, message, kind, link="requests")
    except Exception:  # noqa: BLE001 - a note never fails the action
        logger.debug("request note failed", exc_info=True)


def _requesting_profiles(db, only_pid=None):
    """profiles whose wishlist rows are requests (no download rights)."""
    out = []
    for p in db.get_all_profiles() or []:
        if only_pid is not None and int(p["id"]) != int(only_pid):
            continue
        if not profile_can_download(p):
            out.append(p)
    return out


def _pending_groups(db, only_pid=None):
    groups = []
    for p in _requesting_profiles(db, only_pid):
        groups.extend(group_rows(db.get_pending_request_rows(p["id"]), p["id"], p.get("name") or ""))
    for g in groups:
        g["status"] = "pending"
    groups.sort(key=lambda g: str(g.get("created_at") or ""), reverse=True)
    return groups


def _in_library(db, profile_id):
    from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope

    def check(title, artist):
        if not title:
            return False
        token = set_library_scope(library_scope_for_profile(profile_id))
        try:
            track, confidence = db.check_track_exists(title, artist or "", confidence_threshold=0.8)
            return track is not None
        except Exception:  # noqa: BLE001
            return False
        finally:
            reset_library_scope(token)
    return check


def sweep_fulfillment(db, only_pid=None) -> int:
    """move approved requests whose rows all left the wishlist to available
    (and tell the requester) or removed. approved video requests flip to
    available when their download lands in the library history. returns how
    many changed."""
    changed = 0
    for req in db.list_music_requests(profile_id=only_pid, status="approved"):
        pid = int(req["profile_id"])
        state = fulfillment_status(req.get("tracks") or [],
                                   lambda tid, _pid=pid: db.wishlist_has_track(_pid, tid),
                                   _in_library(db, pid))
        if state and db.set_music_request_status(req["id"], state):
            changed += 1
            if state == "available":
                _notify(pid, f"{describe(req)} is in your library now", "success")
                _event("music_request_available", req)
    for req in db.list_music_video_requests(profile_id=only_pid, status="approved"):
        if db.music_video_downloaded(req["video_id"]) \
                and db.set_music_video_request_status(req["id"], "available"):
            changed += 1
            _notify(int(req["profile_id"]), f"{req.get('title') or 'The video'} is in your library now",
                    "success")
            _event("music_request_available", _video_group(req))
    return changed


def _video_group(req):
    """the shape _event wants for a video request: kind/title/artist/requester."""
    return {"kind": "video", "title": req.get("title") or "",
            "artist": req.get("artist") or "", "requester_name": req.get("requester_name") or ""}


def _find_group(db, profile_id, key):
    for g in _pending_groups(db, only_pid=profile_id):
        if g["key"] == key:
            return g
    return None


@bp.route("/api/requests/music", methods=["GET"])
def list_music_requests():
    """?status=pending|approved|available|declined|all. admins see every
    profile's, members their own."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    admin = is_admin_request()
    scope = None if admin else pid
    status = (request.args.get("status") or "all").lower()
    try:
        sweep_fulfillment(db, only_pid=scope)
    except Exception:  # noqa: BLE001 - the list still shows without the sweep
        logger.exception("music request sweep failed")
    pending = _pending_groups(db, only_pid=scope) if status in ("pending", "all") else []
    history = [] if status == "pending" else db.list_music_requests(
        profile_id=scope, status=None if status == "all" else status)
    for h in history:
        h["track_count"] = len(h.get("tracks") or [])
    video_rows = db.list_music_video_requests(profile_id=scope)
    pending_videos = [r for r in video_rows if r["status"] == "pending"] \
        if status in ("pending", "all") else []
    if status == "pending":
        video_history = []
    elif status == "all":
        video_history = [r for r in video_rows if r["status"] != "pending"]
    else:
        video_history = [r for r in video_rows if r["status"] == status]
    counts = {"pending": len(pending) if status in ("pending", "all") else len(_pending_groups(db, only_pid=scope)),
              "pending_videos": (len(pending_videos) if status in ("pending", "all")
                                 else sum(1 for r in video_rows if r["status"] == "pending"))}
    for h in db.list_music_requests(profile_id=scope):
        counts[h["status"]] = counts.get(h["status"], 0) + 1
    me = db.get_profile(pid) or {}
    return jsonify({"success": True, "pending": pending, "history": history, "counts": counts,
                    "pending_videos": pending_videos, "video_history": video_history,
                    "asks_first": not profile_can_download(me), "quota": _quota(db, me)})


def _quota(db, profile):
    """{limit, days, used, remaining} for a limited asking profile, else None.
    video asks count the same as track asks: an ask is an ask."""
    from core.requests.quota import quota_for, quota_state
    quota = quota_for(profile)
    if not quota or profile_can_download(profile):
        return None
    try:
        conn = db._get_connection()
        try:
            used = len(db.music_request_asks_since(conn.cursor(), profile["id"], quota["days"]))
        finally:
            conn.close()
        used += db.count_music_video_requests_since(profile["id"], quota["days"])
    except Exception:  # noqa: BLE001
        return None
    return quota_state(quota, used)


@bp.route("/api/requests/music/quota", methods=["GET"])
def music_request_quota():
    """before an add: how many asks this profile has left (None = no limit)."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    return jsonify({"success": True, "quota": _quota(db, db.get_profile(pid) or {})})


@bp.route("/api/requests/music/counts", methods=["GET"])
def music_request_counts():
    """the nav badge. admins: requests waiting. members: updates they
    haven't looked at yet (approved, declined, arrived)."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    if is_admin_request():
        videos = len(db.list_music_video_requests(status="pending"))
        return jsonify({"success": True, "pending": len(_pending_groups(db)) + videos, "updates": 0})
    try:
        sweep_fulfillment(db, only_pid=pid)
    except Exception:  # noqa: BLE001
        logger.exception("music request sweep failed")
    unseen = sum(1 for h in db.list_music_requests(profile_id=pid) if not h.get("seen_at"))
    my_videos = db.list_music_video_requests(profile_id=pid)
    pending_videos = sum(1 for r in my_videos if r["status"] == "pending")
    unseen += sum(1 for r in my_videos if r["status"] != "pending" and not r.get("seen_at"))
    return jsonify({"success": True, "pending": len(_pending_groups(db, only_pid=pid)) + pending_videos,
                    "updates": unseen})


@bp.route("/api/requests/music/seen", methods=["POST"])
def music_requests_seen():
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    marked = db.mark_music_requests_seen(pid) + db.mark_music_video_requests_seen(pid)
    return jsonify({"success": True, "marked": marked})


def _group_from_body(db):
    body = request.get_json(silent=True) or {}
    try:
        owner = int(body.get("profile_id"))
    except (TypeError, ValueError):
        return None, None, body
    key = str(body.get("key") or "")
    return owner, (_find_group(db, owner, key) if key else None), body


@bp.route("/api/requests/music/approve", methods=["POST"])
def approve_music_request():
    """{profile_id, key}: the requester's rows become downloadable."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Admin only"}), 403
    db = get_database()
    owner, group, body = _group_from_body(db)
    if not group:
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 404
    flipped = db.approve_request_rows(owner, group["track_ids"])
    if not flipped:
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    db.add_music_request(profile_id=owner, requester_name=group.get("requester_name"),
                         group_key=group["key"], kind=group["kind"], title=group["title"],
                         artist=group.get("artist"), image_url=group.get("image_url"),
                         tracks=group["tracks"], status="approved", resolved_by=get_current_profile_id(),
                         admin_response=(str(body.get("response") or "").strip()[:500] or None))
    _notify(owner, f"{describe(group)} was approved, it's on the way", "success")
    _event("music_request_approved", group)
    return jsonify({"success": True, "approved": flipped})


@bp.route("/api/requests/music/approve-all", methods=["POST"])
def approve_all_music_requests():
    """{profile_id?}: every waiting request (or one profile's) in one go."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Admin only"}), 403
    db = get_database()
    body = request.get_json(silent=True) or {}
    only = body.get("profile_id")
    try:
        only = int(only) if only not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad profile_id"}), 400
    approved = 0
    for group in _pending_groups(db, only_pid=only):
        if db.approve_request_rows(group["profile_id"], group["track_ids"]):
            db.add_music_request(profile_id=group["profile_id"], requester_name=group.get("requester_name"),
                                 group_key=group["key"], kind=group["kind"], title=group["title"],
                                 artist=group.get("artist"), image_url=group.get("image_url"),
                                 tracks=group["tracks"], status="approved",
                                 resolved_by=get_current_profile_id())
            _notify(group["profile_id"], f"{describe(group)} was approved, it's on the way", "success")
            _event("music_request_approved", group)
            approved += 1
    for req in db.list_music_video_requests(profile_id=only, status="pending"):
        ok, _err, _code = _start_approved_video_download(db, req)
        if ok and db.set_music_video_request_status(req["id"], "approved",
                                                    resolved_by=get_current_profile_id()):
            _notify(int(req["profile_id"]),
                    f"{req.get('title') or 'The video'} was approved, it's on the way", "success")
            _event("music_request_approved", _video_group(req))
            approved += 1
    return jsonify({"success": True, "approved": approved})


@bp.route("/api/requests/music/decline", methods=["POST"])
def decline_music_request():
    """{profile_id, key, response?}: rows come off (and stay off: ignore-
    listed so a watchlist scan doesn't file them again)."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Admin only"}), 403
    db = get_database()
    owner, group, body = _group_from_body(db)
    if not group:
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 404
    reason = str(body.get("response") or "").strip()[:500] or None
    _remove_rows(db, owner, group, "request_declined")
    db.add_music_request(profile_id=owner, requester_name=group.get("requester_name"),
                         group_key=group["key"], kind=group["kind"], title=group["title"],
                         artist=group.get("artist"), image_url=group.get("image_url"),
                         tracks=group["tracks"], status="declined", resolved_by=get_current_profile_id(),
                         admin_response=reason)
    _notify(owner, f"{describe(group)} was declined" + (f": {reason}" if reason else ""), "warning")
    _event("music_request_declined", group, reason=reason or "")
    return jsonify({"success": True})


def _remove_rows(db, owner, group, why):
    for t in group["tracks"]:
        db.remove_from_wishlist(t["id"], profile_id=owner)
        try:
            db.add_to_wishlist_ignore(t["id"], track_name=t.get("title") or "",
                                      artist_name=t.get("artist") or "", reason=why, profile_id=owner)
        except Exception:  # noqa: BLE001 - the ignore is a nicety, the removal is the action
            logger.debug("ignore-list add failed for %s", t["id"], exc_info=True)


@bp.route("/api/requests/music/withdraw", methods=["POST"])
def withdraw_music_request():
    """{key}: a member takes back their own waiting request."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    key = str((request.get_json(silent=True) or {}).get("key") or "")
    group = _find_group(db, pid, key) if key else None
    if not group:
        return jsonify({"success": False, "error": "Not found"}), 404
    _remove_rows(db, pid, group, "request_withdrawn")
    return jsonify({"success": True})


@bp.route("/api/requests/music/<int:request_id>", methods=["DELETE"])
def delete_music_request_history(request_id):
    """take a finished request out of the history (own, or any as admin).
    an approved one still downloading keeps downloading."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    ok = get_database().delete_music_request(request_id, profile_id=None if is_admin_request() else pid)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))


# ── music video requests ──────────────────────────────────────────────
# same page, separate storage: videos have no wishlist rows to derive
# pending state from, so the row is both the queue and the history.

@bp.route("/api/requests/music/videos", methods=["POST"])
def file_music_video_request():
    """{video_id, url, title, channel?, thumbnail_url?}: a profile without
    download rights asks for a music video. a profile that can download uses
    /api/music-video/download directly."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    me = db.get_profile(pid) or {}
    if profile_can_download(me):
        return jsonify({"success": False,
                        "error": "Downloads are enabled for this profile, use the download endpoint"}), 403
    body = request.get_json(silent=True) or {}
    video_id = str(body.get("video_id") or "").strip()
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()
    channel = str(body.get("channel") or "").strip() or None
    thumbnail_url = str(body.get("thumbnail_url") or "").strip() or None
    if not video_id or not url or not title:
        return jsonify({"success": False, "error": "video_id, url and title are required"}), 400
    if any(r["video_id"] == video_id
           for r in db.list_music_video_requests(profile_id=pid, status="pending")):
        return jsonify({"success": False, "already": True}), 200
    if db.music_video_downloaded(video_id):
        return jsonify({"success": False, "in_library": True}), 409
    quota = _quota(db, me)
    if quota and quota["remaining"] <= 0:
        from core.requests.quota import quota_for, quota_message
        return jsonify({"success": False, "error": quota_message(quota_for(me)),
                        "quota": True}), 429
    from core.downloads.music_video import parse_artist_title
    artist, _parsed_title = parse_artist_title(title, channel or "")
    rid = db.add_music_video_request(profile_id=pid, requester_name=me.get("name") or "",
                                     video_id=video_id, url=url, title=title, channel=channel,
                                     artist=artist or None, thumbnail_url=thumbnail_url)
    if not rid:
        # None means duplicate or a failed insert: tell them apart so a real
        # error doesn't masquerade as "already requested"
        if any(r["video_id"] == video_id
               for r in db.list_music_video_requests(profile_id=pid, status="pending")):
            return jsonify({"success": False, "already": True}), 200
        logger.error("music video request insert failed for profile %s: %s", pid, title)
        return jsonify({"success": False, "error": "Couldn't save the request"}), 500
    logger.info("music video request %s filed by profile %s: %s", rid, pid, title)
    return jsonify({"success": True, "id": rid})


def _start_approved_video_download(db, req):
    """kick off the download for one pending video request. returns
    (True, None, None) when the video is on its way or already landed (the
    sweep marks it available), else (False, error, code)."""
    starter = globals().get("_start_music_video")
    if starter is None:
        return False, "Video downloads are unavailable", 500
    video_id = str(req.get("video_id") or "")
    if db.music_video_downloaded(video_id):
        return True, None, None
    result = starter({"video_id": video_id, "url": req.get("url") or "",
                      "title": req.get("title") or "", "channel": req.get("channel") or "",
                      "thumbnail": req.get("thumbnail_url") or ""})
    if result.get("error"):
        if result.get("code") == 409:
            return True, None, None  # already downloading: the sweep catches the arrival
        return False, result["error"], result.get("code", 400)
    return True, None, None


@bp.route("/api/requests/music/videos/<int:request_id>/approve", methods=["POST"])
def approve_music_video_request(request_id):
    """admin: a waiting video request starts downloading like a direct one."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Admin only"}), 403
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    if req["status"] != "pending":
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    ok, error, code = _start_approved_video_download(db, req)
    if not ok:
        return jsonify({"success": False, "error": error}), code or 502
    if not db.set_music_video_request_status(request_id, "approved",
                                             resolved_by=get_current_profile_id()):
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    _notify(int(req["profile_id"]),
            f"{req.get('title') or 'The video'} was approved, it's on the way", "success")
    _event("music_request_approved", _video_group(req))
    return jsonify({"success": True})


@bp.route("/api/requests/music/videos/<int:request_id>/decline", methods=["POST"])
def decline_music_video_request(request_id):
    """admin: {response?} — the request ends, the requester hears why."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Admin only"}), 403
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    reason = str((request.get_json(silent=True) or {}).get("response") or "").strip()[:500] or None
    if not db.set_music_video_request_status(request_id, "declined",
                                             resolved_by=get_current_profile_id(),
                                             admin_response=reason):
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    _notify(int(req["profile_id"]),
            f"{req.get('title') or 'The video'} was declined" + (f": {reason}" if reason else ""),
            "warning")
    _event("music_request_declined", _video_group(req), reason=reason or "")
    return jsonify({"success": True})


@bp.route("/api/requests/music/videos/<int:request_id>/withdraw", methods=["POST"])
def withdraw_music_video_request(request_id):
    """a member takes back their own waiting video request (or an admin any)."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not req or req["status"] != "pending":
        return jsonify({"success": False, "error": "Not found"}), 404
    if int(req["profile_id"]) != int(pid) and not is_admin_request():
        return jsonify({"success": False, "error": "Not yours"}), 403
    ok = db.delete_music_video_request(request_id)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))
