"""music requests: the music side of the in-app overseerr.

a profile without download rights adds to its wishlist like anyone else; the
scheduled run only downloads what an admin approved (core/requests/music.py).
this is the queue an admin works through and the history a member reads.

identity is the session's (core.profile_context), never the request body.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.permissions import can, enforcing, may_moderate, may_see_request, profile_can_download, refusal, \
    requires, signed_in
from core.profile_context import get_current_profile_id, is_admin_request
from core.requests import acquisition
from core.requests.acquisition import AWAITING, MANUAL
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


def _event(event_type, group, owner=None, **extra):
    """music_request_approved / _declined / _available for automations. the
    requester's event, never the approver's (an admin or moderator answers
    in their own request)."""
    try:
        from core.app_events import publish
        from core.automation.owners import owned
        publish(event_type, owned({"kind": group.get("kind") or "", "title": group.get("title") or "",
                                   "artist": group.get("artist") or "",
                                   "requester": group.get("requester_name") or "", **extra},
                                  owner if owner is not None else group.get("profile_id")))
    except Exception:  # noqa: BLE001 - an automation hook never fails the request
        logger.debug("music request event failed", exc_info=True)


def _notify(profile_id, message, kind="info"):
    try:
        from core.profile_notify import notify_profile
        notify_profile(profile_id, message, kind, link="requests")
    except Exception:  # noqa: BLE001 - a note never fails the action
        logger.debug("request note failed", exc_info=True)


def _music_mode(db, profile):
    """download | ask | none for a profile row: its music.get under the
    global policy (core.permission_store.music_rights). a database without
    the permission layers (a bare test double) answers from the old column."""
    if not hasattr(db, "get_permission_inputs"):
        return "download" if profile_can_download(profile) else "ask"
    from core.permission_store import music_rights
    return music_rights(db, profile)["mode"]


def _requesting_profiles(db, only_pid=None):
    """profiles whose wishlist rows are requests (they don't get music right
    away: ask first, or no longer allowed to get it at all)."""
    out = []
    for p in db.get_all_profiles() or []:
        if only_pid is not None and int(p["id"]) != int(only_pid):
            continue
        if _music_mode(db, p) != "download":
            out.append(p)
    return out


# ── who sees and answers whose requests (moderators, #2166) ──────────────
# admins: everyone's. a moderator (requests.moderate): everyone's, and may
# approve, decline and tidy anyone's but their own (approving your own ask
# is downloading without the right). requests.see_all: everyone's, read only.
# anyone else: their own.

def _ctx():
    """the caller for may_moderate / may_see_request. a bare test app with no
    permission map answers from the admin flag alone."""
    from core.permissions import context_from_g, make_context
    if enforcing():
        return context_from_g()
    return make_context(get_current_profile_id(), is_admin=is_admin_request(), perms={})


def _sees_everyone():
    if is_admin_request():
        return True
    return may_see_request(_ctx(), None) if enforcing() else False


def _may_answer(owner):
    """may the caller approve / decline / tidy a request ``owner`` filed?"""
    if is_admin_request():
        return True
    return may_moderate(_ctx(), owner)


def _not_a_moderator():
    return refusal("requests.moderate")


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
    # a request held for a release pick (#2199) keeps its rows on the
    # wishlist until the picks land, so it settles the same way
    settling = (db.list_music_requests(profile_id=only_pid, status="approved")
                + db.list_music_requests(profile_id=only_pid, status=AWAITING))
    for req in settling:
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
                and db.mark_music_video_request_available(req["id"]):
            changed += 1
            _notify(int(req["profile_id"]), f"{req.get('title') or 'The video'} is in your library now",
                    "success")
            _event("music_request_available", _video_group(req))
    changed += _sweep_media(db, only_pid)
    return changed


def _sweep_media(db, only_pid=None) -> int:
    """approved book and episode asks flip to available once they're here."""
    from core.requests.media import AUDIOBOOK, book_in_library, describe as describe_media, episode_on_disk
    changed = 0
    # a held book that arrived some other way (someone else's grab, an
    # import) has nothing left to pick: it moves on and settles too
    for req in db.list_media_requests(profile_id=only_pid, status=AWAITING, kind=AUDIOBOOK):
        if book_in_library(req.get("item_key")):
            db.media_request_picked(req["id"])
    for req in db.list_media_requests(profile_id=only_pid, status="approved"):
        here = (book_in_library(req.get("item_key")) if req.get("kind") == AUDIOBOOK
                else episode_on_disk(db, req.get("payload") or {}))
        if here and db.mark_media_request_available(req["id"]):
            changed += 1
            _notify(int(req["profile_id"]), f"{describe_media(req)} is in your library now", "success")
    return changed


def _video_group(req):
    """the shape _event wants for a video request: kind/title/artist/requester."""
    return {"kind": "video", "title": req.get("title") or "",
            "artist": req.get("artist") or "", "requester_name": req.get("requester_name") or "",
            "profile_id": req.get("profile_id")}


def _find_group(db, profile_id, key):
    for g in _pending_groups(db, only_pid=profile_id):
        if g["key"] == key:
            return g
    return None


@bp.route("/api/requests/music", methods=["GET"])
@signed_in
def list_music_requests():
    """?status=pending|approved|available|declined|all. admins see every
    profile's, members their own."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    scope = None if _sees_everyone() else pid
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
    media_rows = db.list_media_requests(profile_id=scope)
    pending_media = [r for r in media_rows if r["status"] == "pending"] \
        if status in ("pending", "all") else []
    if status == "pending":
        media_history = []
    elif status == "all":
        media_history = [r for r in media_rows if r["status"] != "pending"]
    else:
        media_history = [r for r in media_rows if r["status"] == status]
    counts["pending_media"] = sum(1 for r in media_rows if r["status"] == "pending")
    me = db.get_profile(pid) or {}
    mode = _music_mode(db, me)
    return jsonify({"success": True, "pending": pending, "history": history, "counts": counts,
                    "pending_videos": pending_videos, "video_history": video_history,
                    "pending_media": [_media_public(r) for r in pending_media],
                    "media_history": [_media_public(r) for r in media_history],
                    "asks_first": mode != "download", "can_request": mode != "none",
                    "moderator": _may_answer(None) and not is_admin_request(),
                    "acquisition": acquisition.modes(),
                    "awaiting": _awaiting_count(db) if _may_answer(None) else 0,
                    "quota": _quota(db, me)})


def _quota(db, profile):
    """{limit, days, used, remaining} for a limited asking profile, else None.
    the music limit: album, track and music video asks (movies and TV have
    their own)."""
    from core.requests.quota import quota_for, quota_state
    if hasattr(db, "get_permission_inputs") and profile:
        from core.permission_store import music_rights
        quota = music_rights(db, profile)["quota"]
    else:
        quota = None if profile_can_download(profile) else quota_for(profile)
    if not quota:
        return None
    from core.requests.asks import asks_used
    used = asks_used(profile["id"], quota["days"], "music", music_db=db)
    if used is None:  # fail closed: quota unknown means no asks allowed
        logger.warning("quota check failed, denying asks")
        return {"limit": quota["limit"], "days": quota["days"], "used": quota["limit"], "remaining": 0}
    return quota_state(quota, used)


@bp.route("/api/requests/music/quota", methods=["GET"])
@signed_in
def music_request_quota():
    """before an add: how many asks this profile has left (None = no limit)."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    return jsonify({"success": True, "quota": _quota(db, db.get_profile(pid) or {})})


@bp.route("/api/requests/music/counts", methods=["GET"])
@signed_in
def music_request_counts():
    """the nav badge. admins: requests waiting. members: updates they
    haven't looked at yet (approved, declined, arrived)."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    if is_admin_request():
        videos = len(db.list_music_video_requests(status="pending"))
        media = len(db.list_media_requests(status="pending"))
        # approved asks waiting for a release pick (#2199) are the admin's too
        return jsonify({"success": True,
                        "pending": len(_pending_groups(db)) + videos + media + _awaiting_count(db),
                        "updates": 0})
    if _may_answer(None):
        # a moderator's badge: everyone else's waiting requests, plus news on
        # their own like any member
        waiting = sum(1 for g in _pending_groups(db) if int(g["profile_id"]) != int(pid))
        waiting += sum(1 for r in db.list_music_video_requests(status="pending")
                       if int(r["profile_id"]) != int(pid))
        waiting += sum(1 for r in db.list_media_requests(status="pending")
                       if int(r["profile_id"]) != int(pid))
        waiting += _awaiting_count(db)
        unseen = sum(1 for h in db.list_music_requests(profile_id=pid) if not h.get("seen_at"))
        unseen += sum(1 for r in db.list_music_video_requests(profile_id=pid)
                      if r["status"] != "pending" and not r.get("seen_at"))
        unseen += sum(1 for r in db.list_media_requests(profile_id=pid)
                      if r["status"] != "pending" and not r.get("seen_at"))
        return jsonify({"success": True, "pending": waiting, "updates": unseen, "moderator": True})
    try:
        sweep_fulfillment(db, only_pid=pid)
    except Exception:  # noqa: BLE001
        logger.exception("music request sweep failed")
    unseen = sum(1 for h in db.list_music_requests(profile_id=pid) if not h.get("seen_at"))
    my_videos = db.list_music_video_requests(profile_id=pid)
    pending_videos = sum(1 for r in my_videos if r["status"] == "pending")
    unseen += sum(1 for r in my_videos if r["status"] != "pending" and not r.get("seen_at"))
    my_media = db.list_media_requests(profile_id=pid)
    pending_media = sum(1 for r in my_media if r["status"] == "pending")
    unseen += sum(1 for r in my_media if r["status"] != "pending" and not r.get("seen_at"))
    return jsonify({"success": True,
                    "pending": len(_pending_groups(db, only_pid=pid)) + pending_videos + pending_media,
                    "updates": unseen})


@bp.route("/api/requests/music/seen", methods=["POST"])
@signed_in
def music_requests_seen():
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    marked = (db.mark_music_requests_seen(pid) + db.mark_music_video_requests_seen(pid)
              + db.mark_media_requests_seen(pid))
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
@signed_in
def approve_music_request():
    """{profile_id, key}: the requester's rows become downloadable."""
    db = get_database()
    owner, group, body = _group_from_body(db)
    if not _may_answer(owner):
        return _not_a_moderator()
    if not group:
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 404
    held = acquisition.resolve("music", acquisition.override_from(body)) == MANUAL
    flipped = _approve_group(db, owner, group, held,
                             response=(str(body.get("response") or "").strip()[:500] or None))
    if not flipped:
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    return jsonify({"success": True, "approved": flipped, "acquisition": MANUAL if held else "auto"})


def _approve_group(db, owner, group, held, response=None):
    """flip one waiting group: approved (the scheduled run downloads it) or
    held for a release pick (#2199). records the history, tells the
    requester. returns how many rows moved."""
    status = AWAITING if held else "approved"
    flipped = db.approve_request_rows(owner, group["track_ids"], status=status)
    if not flipped:
        return 0
    db.add_music_request(profile_id=owner, requester_name=group.get("requester_name"),
                         group_key=group["key"], kind=group["kind"], title=group["title"],
                         artist=group.get("artist"), image_url=group.get("image_url"),
                         tracks=group["tracks"], status=status, resolved_by=get_current_profile_id(),
                         admin_response=response)
    _notify(owner, f"{describe(group)} was approved, " +
            ("a release gets picked for it next" if held else "it's on the way"), "success")
    _event("music_request_approved", group, owner=owner)
    return flipped


@bp.route("/api/requests/music/approve-all", methods=["POST"])
@signed_in
def approve_all_music_requests():
    """{profile_id?}: every waiting request (or one profile's) in one go. a
    moderator's "all" is everyone's but their own."""
    if not _may_answer(None):
        return _not_a_moderator()
    db = get_database()
    body = request.get_json(silent=True) or {}
    only = body.get("profile_id")
    try:
        only = int(only) if only not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad profile_id"}), 400
    if only is not None and not _may_answer(only):
        return _not_a_moderator()
    approved = 0
    # each kind's own setting: approve all never picks a release for anyone
    held = acquisition.mode_for("music") == MANUAL
    for group in _pending_groups(db, only_pid=only):
        if not _may_answer(group["profile_id"]):
            continue
        if _approve_group(db, group["profile_id"], group, held):
            approved += 1
    for req in db.list_music_video_requests(profile_id=only, status="pending"):
        if not _may_answer(req["profile_id"]):
            continue
        ok, _err, _code = _start_approved_video_download(db, req)
        if ok and db.set_music_video_request_status(req["id"], "approved",
                                                    resolved_by=get_current_profile_id()):
            _notify(int(req["profile_id"]),
                    f"{req.get('title') or 'The video'} was approved, it's on the way", "success")
            _event("music_request_approved", _video_group(req))
            approved += 1
    for req in db.list_media_requests(profile_id=only, status="pending"):
        if not _may_answer(req["profile_id"]):
            continue
        if _approve_media(db, req) is None:
            approved += 1
    return jsonify({"success": True, "approved": approved})


@bp.route("/api/requests/music/decline", methods=["POST"])
@signed_in
def decline_music_request():
    """{profile_id, key, response?}: rows come off (and stay off: ignore-
    listed so a watchlist scan doesn't file them again)."""
    db = get_database()
    owner, group, body = _group_from_body(db)
    if not _may_answer(owner):
        return _not_a_moderator()
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
    _event("music_request_declined", group, owner=owner, reason=reason or "")
    return jsonify({"success": True})


def _remove_rows(db, owner, group, why):
    descriptors = []
    for t in group["tracks"]:
        try:
            descriptors.extend(db.get_wishlist_tracks(profile_id=owner, track_key=str(t["id"])))
        except Exception:  # noqa: BLE001 - a missed descriptor only skips the release
            logger.debug("wishlist descriptor read failed for %s", t["id"], exc_info=True)
    for t in group["tracks"]:
        db.remove_from_wishlist(t["id"], profile_id=owner)
        try:
            db.add_to_wishlist_ignore(t["id"], track_name=t.get("title") or "",
                                      artist_name=t.get("artist") or "", reason=why, profile_id=owner)
        except Exception:  # noqa: BLE001 - the ignore is a nicety, the removal is the action
            logger.debug("ignore-list add failed for %s", t["id"], exc_info=True)
    # captured before, released after: same order as every wishlist removal
    _release_wishes(db, owner, descriptors)


def _release_wishes(db, owner, descriptors):
    """a declined or withdrawn request stops being wanted. left behind, the
    hourly reconcile re-added it once the ignore aged out, and for a profile
    with its own library the mirror filed it approved: it downloaded"""
    if not descriptors:
        return
    try:
        from core.settings import config_manager
        from core.library2.monitor_sync import sync_wishlist_removal
        sync_wishlist_removal(db, config_manager, descriptors, profile_id=owner)
    except Exception:  # noqa: BLE001 - the removal is the action, the release a follow-up
        logger.debug("request wish release failed for profile %s", owner, exc_info=True)


@bp.route("/api/requests/music/withdraw", methods=["POST"])
@signed_in
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
@signed_in
def delete_music_request_history(request_id):
    """take a finished request out of the history (own, or any as admin).
    an approved one still downloading keeps downloading."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    scope = pid
    if is_admin_request():
        scope = None
    elif _may_answer(None):
        # a moderator tidies anyone's finished request but their own goes the
        # member's way (scope = them) anyway
        row = next((h for h in db.list_music_requests() if int(h.get("id") or 0) == int(request_id)), None)
        if row is not None and _may_answer(row.get("profile_id")):
            scope = None
    ok = db.delete_music_request(request_id, profile_id=scope)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))


# ── music video requests ──────────────────────────────────────────────
# same page, separate storage: videos have no wishlist rows to derive
# pending state from, so the row is both the queue and the history.

@bp.route("/api/requests/music/videos", methods=["POST"])
@requires("music.videos")
def file_music_video_request():
    """{video_id, url, title, channel?, thumbnail_url?}: a profile without
    download rights asks for a music video. a profile that can download uses
    /api/music-video/download directly."""
    db = get_database()
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    me = db.get_profile(pid) or {}
    mode = _music_mode(db, me)
    if mode == "download":
        return jsonify({"success": False,
                        "error": "Downloads are enabled for this profile, use the download endpoint"}), 403
    if mode == "none":
        return refusal("music.get")
    # a music video carries no explicit flag: a profile whose explicit music
    # is hidden can't vouch for one, so it doesn't ask for them (S17)
    if enforcing() and can(None, "music.hide_explicit"):
        return jsonify({"success": False, "error": "restricted", "restricted": True}), 403
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
        from core.requests.quota import quota_message
        return jsonify({"success": False, "error": quota_message(quota, "music"),
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
@signed_in
def approve_music_video_request(request_id):
    """admin or moderator: a waiting video request starts downloading like a direct one."""
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not _may_answer(req["profile_id"] if req else None):
        return _not_a_moderator()
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
@signed_in
def decline_music_video_request(request_id):
    """admin or moderator: {response?} — the request ends, the requester hears why."""
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not _may_answer(req["profile_id"] if req else None):
        return _not_a_moderator()
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
@signed_in
def withdraw_music_video_request(request_id):
    """a member takes back their own waiting video request (or an admin any)."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not req or req["status"] != "pending":
        return jsonify({"success": False, "error": "Not found"}), 404
    if int(req["profile_id"]) != int(pid) and not _may_answer(req["profile_id"]):
        return jsonify({"success": False, "error": "Not yours"}), 403
    ok = db.delete_music_video_request(request_id)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))


@bp.route("/api/requests/music/videos/<int:request_id>", methods=["DELETE"])
@signed_in
def delete_music_video_request_history(request_id):
    """take a finished video request out of the history (own, or any as admin)."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    req = db.get_music_video_request(request_id)
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    if int(req.get("profile_id") or 0) != int(pid) and not _may_answer(req.get("profile_id")):
        return jsonify({"success": False, "error": "Not found"}), 404
    if req.get("status") == "pending":
        return jsonify({"success": False, "error": "Withdraw a pending request instead"}), 409
    ok = db.delete_music_video_request(request_id)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))


# ── audiobook and podcast episode requests (#2188) ─────────────────────
# a profile on Ask first for books or episodes asks from the audiobook and
# podcast pages (api/audiobooks.py, api/podcasts.py); the asks are answered
# here, on the same page and by the same people as music.

def _media_public(req):
    """a request as the page reads it: the payload stays on the server."""
    return {k: v for k, v in req.items() if k != "payload"}


def _media_held(req, override=None) -> bool:
    """does this approval hold the ask for a release pick (#2199)? books
    only: an episode is one file, nothing to pick between."""
    from core.requests import media
    if req.get("kind") != media.AUDIOBOOK or acquisition.resolve("audiobook", override) != MANUAL:
        return False
    # already in the library: nothing to pick (the grab refuses an owned
    # book), so it goes the automatic way and settles as arrived
    return not media.book_in_library(req.get("item_key"))


def _approve_media(db, req, override=None):
    """start what the approval means, then mark it. a book held for a
    release pick starts nothing: it waits on the Requests page until someone
    picks one. None on success, else (error, status)."""
    from core.requests.media import describe as describe_media, start_approved
    if _media_held(req, override):
        if not db.set_media_request_status(req["id"], AWAITING, resolved_by=get_current_profile_id()):
            return "That request isn't waiting any more", 409
        _notify(int(req["profile_id"]), f"{describe_media(req)} was approved, a release gets picked for it next",
                "success")
        return None
    error = start_approved(req)
    if error:
        return error, 502
    if not db.set_media_request_status(req["id"], "approved", resolved_by=get_current_profile_id()):
        return "That request isn't waiting any more", 409
    _notify(int(req["profile_id"]), f"{describe_media(req)} was approved, it's on the way", "success")
    return None


@bp.route("/api/requests/media/<int:request_id>/approve", methods=["POST"])
@signed_in
def approve_media_request(request_id):
    """admin or moderator: a waiting book goes on their wishlist, an episode
    starts downloading."""
    db = get_database()
    req = db.get_media_request(request_id)
    if not _may_answer(req["profile_id"] if req else None):
        return _not_a_moderator()
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    if req["status"] != "pending":
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    override = acquisition.override_from(request.get_json(silent=True) or {})
    failed = _approve_media(db, req, override)
    if failed:
        return jsonify({"success": False, "error": failed[0]}), failed[1]
    return jsonify({"success": True, "acquisition": MANUAL if _media_held(req, override) else "auto"})


@bp.route("/api/requests/media/<int:request_id>/decline", methods=["POST"])
@signed_in
def decline_media_request(request_id):
    """admin or moderator: {response?}. the request ends, the requester hears why."""
    from core.requests.media import describe as describe_media
    db = get_database()
    req = db.get_media_request(request_id)
    if not _may_answer(req["profile_id"] if req else None):
        return _not_a_moderator()
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    reason = str((request.get_json(silent=True) or {}).get("response") or "").strip()[:500] or None
    if not db.set_media_request_status(request_id, "declined", resolved_by=get_current_profile_id(),
                                       admin_response=reason):
        return jsonify({"success": False, "error": "That request isn't waiting any more"}), 409
    _notify(int(req["profile_id"]), f"{describe_media(req)} was declined" + (f": {reason}" if reason else ""),
            "warning")
    return jsonify({"success": True})


@bp.route("/api/requests/media/<int:request_id>/withdraw", methods=["POST"])
@signed_in
def withdraw_media_request(request_id):
    """a member takes back their own waiting request (or a moderator anyone's)."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    req = db.get_media_request(request_id)
    if not req or req["status"] != "pending":
        return jsonify({"success": False, "error": "Not found"}), 404
    if int(req["profile_id"]) != int(pid) and not _may_answer(req["profile_id"]):
        return jsonify({"success": False, "error": "Not yours"}), 403
    ok = db.delete_media_request(request_id)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))


@bp.route("/api/requests/media/<int:request_id>", methods=["DELETE"])
@signed_in
def delete_media_request_history(request_id):
    """take a finished request out of the history (own, or any as admin)."""
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required"}), 401
    db = get_database()
    req = db.get_media_request(request_id)
    if not req:
        return jsonify({"success": False, "error": "Not found"}), 404
    if int(req.get("profile_id") or 0) != int(pid) and not _may_answer(req.get("profile_id")):
        return jsonify({"success": False, "error": "Not found"}), 404
    if req.get("status") == "pending":
        return jsonify({"success": False, "error": "Withdraw a pending request instead"}), 409
    ok = db.delete_media_request(request_id)
    return (jsonify({"success": True}) if ok else (jsonify({"success": False, "error": "Not found"}), 404))



# ── approved requests that wait for a release pick (#2199) ─────────────
# Settings: one mode per kind (core/requests/acquisition.py). in manual mode
# an approval holds the ask: music rows sit on the requester's wishlist as
# 'awaiting_selection', a book's ask as 'awaiting_selection' with nothing on
# the wishlist yet. no automatic pass downloads either. the queue below is
# where an admin (or a moderator, for anyone's but their own) picks.

@bp.route("/api/requests/acquisition", methods=["GET"])
@signed_in
def get_request_acquisition():
    """{modes: {music, movie, tv, audiobook}: auto | manual}. anyone may read
    it (the approve buttons word themselves from it); only admins change it."""
    return jsonify({"success": True, "modes": acquisition.modes(), "may_change": bool(is_admin_request())})


@bp.route("/api/requests/acquisition", methods=["PUT"])
@signed_in
def set_request_acquisition():
    """admin: {modes: {kind: auto | manual}} for the kinds that change."""
    if not is_admin_request():
        return jsonify({"success": False, "error": "Only an admin changes how approved requests download."}), 403
    body = request.get_json(silent=True) or {}
    try:
        modes = acquisition.set_modes(body.get("modes") or {})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    logger.info("approved request downloads set to %s", modes)
    return jsonify({"success": True, "modes": modes})


def _awaiting_groups(db, only_pid=None):
    """music asks held for a pick, every profile's (a profile's rights may
    have changed since the approval, the hold stays)."""
    groups = []
    for p in db.get_all_profiles() or []:
        if only_pid is not None and int(p["id"]) != int(only_pid):
            continue
        rows = db.get_awaiting_request_rows(p["id"])
        if not rows:
            continue
        approved = {}
        for r in rows:
            approved[str(r.get("spotify_track_id"))] = r.get("approved_at")
        for g in group_rows(rows, p["id"], p.get("name") or ""):
            g["status"] = AWAITING
            g["approved_at"] = max((str(approved.get(t) or "") for t in g["track_ids"]), default="") or None
            groups.append(g)
    groups.sort(key=lambda g: str(g.get("approved_at") or ""), reverse=True)
    return groups


def _awaiting_media(db):
    from core.requests.media import AUDIOBOOK
    return db.list_media_requests(status=AWAITING, kind=AUDIOBOOK)


def _awaiting_count(db) -> int:
    """what the caller may pick, for the badge and the page's tab."""
    try:
        n = sum(1 for g in _awaiting_groups(db) if _may_answer(g["profile_id"]))
        n += sum(1 for r in _awaiting_media(db) if _may_answer(r["profile_id"]))
        return n
    except Exception:  # noqa: BLE001 - a count never fails the page
        logger.debug("awaiting request count failed", exc_info=True)
        return 0


@bp.route("/api/requests/awaiting", methods=["GET"])
@signed_in
def list_awaiting_requests():
    """the pick queue: music asks and book asks approved on hold, the ones
    the caller may pick (admins every one, a moderator anyone's but their
    own). movies and TV keep theirs on the video side
    (/api/video/requests/awaiting)."""
    if not _may_answer(None):
        return _not_a_moderator()
    db = get_database()
    music = [g for g in _awaiting_groups(db) if _may_answer(g["profile_id"])]
    media = [_media_public(r) for r in _awaiting_media(db) if _may_answer(r["profile_id"])]
    for r in media:
        r["asin"] = r.get("item_key")
    return jsonify({"success": True, "music": music, "media": media, "modes": acquisition.modes()})


def _find_awaiting_group(db, owner, key):
    for g in _awaiting_groups(db, only_pid=owner):
        if g["key"] == key:
            return g
    return None


def music_pick_started(db, owner, track_id) -> None:
    """a release was picked for one held track: the request's history moves
    on (awaiting -> approved, the requester hears it's on the way). the row
    itself stays held until the picked file lands and takes it off the
    wishlist, so a failed pick can be picked again and nothing automatic
    ever reaches for it. called by the pick download (web_server.py)."""
    from core.requests.music import group_key
    row = db.get_wishlist_track(str(track_id), profile_id=int(owner)) or {}
    data = row.get("spotify_data")
    key = group_key({"spotify_track_id": row.get("spotify_track_id") or track_id,
                     "spotify_data": data, "source_type": row.get("source_type")})
    if db.music_request_picked(int(owner), key):
        req = next((h for h in db.list_music_requests(profile_id=int(owner))
                    if h.get("group_key") == key), None)
        if req:
            _notify(int(owner), f"{describe(req)} is on the way", "success")


def held_music_track(db, owner, track_id):
    """the wishlist row of a held track, or None (not held, not theirs)."""
    row = db.get_wishlist_track(str(track_id), profile_id=int(owner))
    if not row or row.get("request_status") != AWAITING:
        return None
    return row


def may_pick(owner) -> bool:
    """may the caller pick a release for ``owner``'s request? the same people
    who may answer it (#2166)."""
    return _may_answer(owner)


@bp.route("/api/requests/music/awaiting/auto", methods=["POST"])
@signed_in
def release_music_request_to_auto():
    """{profile_id, key}: stop waiting for a pick, the scheduled run
    downloads it like any approved request."""
    db = get_database()
    body = request.get_json(silent=True) or {}
    try:
        owner = int(body.get("profile_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad profile_id"}), 400
    if not _may_answer(owner):
        return _not_a_moderator()
    group = _find_awaiting_group(db, owner, str(body.get("key") or ""))
    if not group:
        return jsonify({"success": False, "error": "That request isn't waiting for a pick any more"}), 404
    moved = db.release_awaiting_request_rows(owner, group["track_ids"])
    if not moved:
        return jsonify({"success": False, "error": "That request isn't waiting for a pick any more"}), 409
    if db.music_request_picked(owner, group["key"]):
        _notify(owner, f"{describe(group)} is on the way", "success")
    return jsonify({"success": True, "released": moved})


def _held_media(db, request_id):
    """(request, None) for a held book the caller may pick, else (None, response)."""
    req = db.get_media_request(request_id)
    if not _may_answer(req["profile_id"] if req else None):
        return None, _not_a_moderator()
    if not req or req.get("status") != AWAITING:
        return None, (jsonify({"success": False, "error": "That request isn't waiting for a pick any more"}), 404)
    return req, None


@bp.route("/api/requests/media/<int:request_id>/picked", methods=["POST"])
@signed_in
def media_request_picked(request_id):
    """a release was grabbed for a held book (the release picker's own grab,
    /api/audiobooks/grab): the request moves on to approved, and arrives
    like any other once the book is in the library."""
    from core.requests.media import describe as describe_media
    db = get_database()
    req, refused = _held_media(db, request_id)
    if refused:
        return refused
    if not db.media_request_picked(request_id):
        return jsonify({"success": False, "error": "That request isn't waiting for a pick any more"}), 409
    _notify(int(req["profile_id"]), f"{describe_media(req)} is on the way", "success")
    return jsonify({"success": True})


@bp.route("/api/requests/media/<int:request_id>/auto", methods=["POST"])
@signed_in
def release_media_request_to_auto(request_id):
    """stop waiting for a pick: the book goes on the requester's wishlist and
    the wishlist pass grabs it, as an automatic approval would have."""
    from core.requests.media import describe as describe_media, start_approved
    db = get_database()
    req, refused = _held_media(db, request_id)
    if refused:
        return refused
    error = start_approved(req)
    if error:
        return jsonify({"success": False, "error": error}), 502
    if not db.media_request_picked(request_id):
        return jsonify({"success": False, "error": "That request isn't waiting for a pick any more"}), 409
    _notify(int(req["profile_id"]), f"{describe_media(req)} is on the way", "success")
    return jsonify({"success": True})
