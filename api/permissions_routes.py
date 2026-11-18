"""people and permissions api (#2166, #2167, #2168). the contract is
scrap/permissions/API.md; the rules are core/permissions.py and the storage
glue core/permission_store.py.

every route is admin only except GET /api/profiles/me/permissions, which
only ever answers about the caller.
"""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from core import permission_store as store
from core import permissions as P
from core.permissions import self_only
from core.profile_context import admin_only, get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("api.permissions")

bp = Blueprint("permissions", __name__)

get_database = None
get_video_db = None
config_get = None


def configure(*, get_database, get_video_db=None, config_get=None):
    globals()["get_database"] = get_database
    globals()["get_video_db"] = get_video_db
    globals()["config_get"] = config_get


def _signup_moved(db):
    """the old System > Security sign-up settings become the sign-up
    template the first time an admin looks (store.move_signup_settings)."""
    if config_get is not None:
        store.move_signup_settings(db, config_get)


def create_blueprint():
    return bp


def _err(exc: store.PermissionRequestError):
    return jsonify({"success": False, "error": exc.code, "message": str(exc)}), exc.status


def _unavailable(exc):
    logger.error("permissions unavailable: %s", exc)
    return jsonify({"success": False, "error": "permissions_unavailable",
                    "message": "Couldn't read permissions right now. Try again in a moment."}), 503


def _body():
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise store.PermissionRequestError("The body is a JSON object.")
    return data


def _dry_run() -> bool:
    return str(request.args.get("dry_run", "")).lower() in ("1", "true", "yes")


def _audit(action, target_id=None, target_name=None, detail=None):
    try:
        from api.profile_admin import audit
        audit(action, target_id=target_id, target_name=target_name,
              detail=json.dumps(detail, sort_keys=True)[:2000] if detail is not None else None)
    except Exception as exc:  # noqa: BLE001 - the log never fails the change
        logger.debug("permissions audit failed: %s", exc)


@bp.route("/api/permissions/catalogue", methods=["GET"])
@admin_only
def catalogue():
    return jsonify(dict(P.catalogue_payload(), success=True))


@bp.route("/api/permissions/quality-options", methods=["GET"])
@admin_only
def quality_options():
    """the quality profiles each quality row can pick from, with media type,
    display tags and a short range line ("720p to 1080p"). video Default
    (id 0) is always there. the video list is the request resolver's own
    (core.video.quality_profile.quality_options), so the 4K tag here is the
    one "4K always asks" applies to."""
    out = {"music": [], "movies": [], "tv": []}
    try:
        for qp in get_database().list_quality_profiles():
            out["music"].append({"id": qp.get("id"), "name": qp.get("name"),
                                 "is_default": bool(qp.get("is_default"))})
    except Exception as exc:  # noqa: BLE001
        logger.debug("music quality profiles unreadable: %s", exc)
    rows = [{"id": 0, "name": "Default", "media_type": None, "tags": [], "range": ""}]
    try:
        if get_video_db is not None:
            from core.video.quality_profile import quality_options as video_options
            rows = video_options(get_video_db())
    except Exception as exc:  # noqa: BLE001
        logger.debug("video quality profiles unreadable: %s", exc)
    out["movies"] = [r for r in rows if r.get("media_type") in (None, "movie")]
    out["tv"] = [r for r in rows if r.get("media_type") in (None, "tv")]
    return jsonify(dict(out, success=True))


@bp.route("/api/permissions/policy", methods=["GET"])
@admin_only
def get_policy():
    try:
        return jsonify(store.policy_payload(get_database()))
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/permissions/policy", methods=["PUT"])
@admin_only
def put_policy():
    try:
        body = _body()
        dry = _dry_run()
        out = store.change_policy(get_database(), body, dry_run=dry)
        if not dry:
            _audit("permissions_policy", detail={"set": body.get("set"), "reset": body.get("reset")})
        return jsonify(out)
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/permissions/templates", methods=["GET"])
@admin_only
def list_templates():
    db = get_database()
    _signup_moved(db)
    return jsonify(store.templates_payload(db))


@bp.route("/api/permissions/templates", methods=["POST"])
@admin_only
def create_template():
    try:
        out = store.create_template(get_database(), _body())
        _audit("permissions_template_create", target_name=out["template"]["name"],
               detail={"id": out["template"]["id"]})
        return jsonify(out), 201
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/permissions/templates/<int:template_id>", methods=["PUT"])
@admin_only
def change_template(template_id):
    try:
        body = _body()
        dry = _dry_run()
        out = store.change_template(get_database(), template_id, body, dry_run=dry)
        if not dry:
            _audit("permissions_template", target_name=out["template"]["name"],
                   detail={"id": template_id, "perms": body.get("perms"), "reset": body.get("reset")})
        return jsonify(out)
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/permissions/templates/<int:template_id>", methods=["DELETE"])
@admin_only
def delete_template(template_id):
    try:
        reassign = request.args.get("reassign_to")
        if reassign is None:
            reassign = _body().get("reassign_to")
        out = store.delete_template(get_database(), template_id, reassign)
        _audit("permissions_template_delete", detail={"id": template_id, "reassign_to": reassign})
        return jsonify(out)
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/permissions/signup-template", methods=["PUT"])
@admin_only
def put_signup_template():
    try:
        db = get_database()
        # an admin's own pick wins over the old settings, so they move first
        _signup_moved(db)
        return jsonify(store.set_signup_template(db, _body().get("template_id")))
    except store.PermissionRequestError as exc:
        return _err(exc)


@bp.route("/api/permissions/asks-used", methods=["GET"])
@admin_only
def asks_used():
    """the People list's Requests column (#2166 follow-up 10): for every
    member with a request limit, the asks spent in each limited kind's
    window (core/requests/asks.py, the same count every limit check uses).
    {"used": {"<id>": {"music"|"movies"|"tv": {kind, limit, days, used,
    remaining}}}}; a kind whose count can't be read is left out (the row
    shows just the limit)."""
    from core.requests import asks
    db = get_database()
    try:
        video_db = get_video_db() if get_video_db is not None else None
    except Exception:  # noqa: BLE001 - the video kinds are left out below
        video_db = None
    out = {}
    for prof in db.get_all_profiles():
        if P.is_admin_row(prof):
            continue
        try:
            eff, _source = store.effective_for(db, prof)
        except store.PermissionsUnavailable:
            continue
        kinds = {}
        for kind in asks.KINDS:
            quota = asks.quota_for(eff, kind)
            if quota is None or (kind != "music" and video_db is None):
                continue
            used = asks.asks_used(prof["id"], quota["days"], kind, perms=eff, music_db=db, video_db=video_db)
            if used is None:
                continue
            kinds[kind] = {"kind": kind, "limit": quota["limit"], "days": quota["days"], "used": int(used),
                           "remaining": max(0, quota["limit"] - int(used))}
        if kinds:
            out[str(prof["id"])] = kinds
    return jsonify({"success": True, "used": out})


def _profile_or_404(profile_id):
    prof = get_database().get_profile(int(profile_id))
    if not prof:
        raise store.PermissionRequestError("No such profile.", 404, "no_profile")
    return prof


@bp.route("/api/profiles/<int:profile_id>/permissions", methods=["GET"])
@admin_only
def get_profile_permissions(profile_id):
    try:
        return jsonify(store.profile_payload(get_database(), _profile_or_404(profile_id)))
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/profiles/<int:profile_id>/permissions", methods=["PUT"])
@admin_only
def put_profile_permissions(profile_id):
    try:
        body = _body()
        prof = _profile_or_404(profile_id)
        out = store.change_profile(get_database(), prof, body)
        if out.get("admin_changed"):
            # the same words the profiles route logs (api/user_profiles.py)
            _audit("admin_granted" if body.get("is_admin") else "admin_revoked",
                   target_id=profile_id, target_name=prof.get("name"))
        if not body.get("is_admin"):
            _audit("permissions_profile", target_id=profile_id, target_name=prof.get("name"),
                   detail={"template_id": body.get("template_id"), "set": body.get("set"),
                           "reset": body.get("reset"), "reset_categories": body.get("reset_categories")})
        return jsonify(out)
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


@bp.route("/api/profiles/me/asks", methods=["GET"])
@self_only
def my_asks():
    """the caller's own asks per limited kind, for Profile & account's
    "5 asks a week, 2 used": {"asks": {"music"|"movies"|"tv": {kind, limit,
    days, used, remaining}}}. only kinds with a limit; admins have none. a
    count that can't be read reads as the limit spent (the same answer the
    ask itself gets)."""
    from core.requests import asks
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required", "profile_required": True}), 401
    db = get_database()
    try:
        prof = db.get_profile(int(pid), raise_errors=True)
        if not prof:
            return jsonify({"success": False, "error": "profile_required", "profile_required": True}), 401
        if P.is_admin_row(prof):
            return jsonify({"success": True, "asks": {}})
        eff, _source = store.effective_for(db, prof)
    except Exception as exc:  # noqa: BLE001 - PermissionsUnavailable or a db that can't answer
        return _unavailable(exc)
    out = {}
    for kind in asks.KINDS:
        if asks.quota_for(eff, kind) is None:
            continue
        video_db = None
        if kind != "music":
            try:
                video_db = get_video_db() if get_video_db is not None else None
            except Exception:  # noqa: BLE001 - counted as unreadable
                video_db = None
            if video_db is None:
                q = asks.quota_for(eff, kind)
                out[kind] = {"kind": kind, "limit": q["limit"], "days": q["days"], "used": q["limit"],
                             "remaining": 0}
                continue
        out[kind] = asks.status(int(pid), eff, kind, music_db=db, video_db=video_db)
    return jsonify({"success": True, "asks": out})


@bp.route("/api/profiles/me/permissions", methods=["GET"])
@self_only
def my_permissions():
    pid = get_current_profile_id()
    if pid is None:
        return jsonify({"success": False, "error": "profile_required", "profile_required": True}), 401
    try:
        prof = get_database().get_profile(int(pid), raise_errors=True)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(exc)
    if not prof:
        return jsonify({"success": False, "error": "profile_required", "profile_required": True}), 401
    if prof.get("disabled") and not P.is_admin_row(prof):
        return jsonify({"success": False, "error": "profile_required", "disabled": True}), 401
    try:
        return jsonify(store.me_payload(get_database(), prof))
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)
