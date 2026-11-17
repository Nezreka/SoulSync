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


def configure(*, get_database, get_video_db=None):
    globals()["get_database"] = get_database
    globals()["get_video_db"] = get_video_db


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
    """the quality profiles each quality row can pick from, with media type
    and display tags. video Default (id 0) is always there."""
    out = {"music": [], "movies": [], "tv": []}
    try:
        for qp in get_database().list_quality_profiles():
            out["music"].append({"id": qp.get("id"), "name": qp.get("name"),
                                 "is_default": bool(qp.get("is_default"))})
    except Exception as exc:  # noqa: BLE001
        logger.debug("music quality profiles unreadable: %s", exc)
    video = []
    try:
        if get_video_db is not None:
            video = get_video_db().named_quality_profiles()
    except Exception as exc:  # noqa: BLE001
        logger.debug("video quality profiles unreadable: %s", exc)
    rows = [{"id": 0, "name": "Default", "media_type": None, "tags": []}]
    for qp in video:
        tags = []
        try:
            blob = json.loads(qp.get("items") or "{}")
            if isinstance(blob, dict) and str(blob.get("library") or "").lower() == "4k":
                tags.append("4k")
        except (TypeError, ValueError):
            pass
        if "extended" in str(qp.get("name") or "").lower():
            tags.append("extended")
        rows.append({"id": qp.get("id"), "name": qp.get("name"), "media_type": qp.get("media_type"),
                     "tags": tags})
    out["movies"] = [r for r in rows if r["media_type"] in (None, "movie")]
    out["tv"] = [r for r in rows if r["media_type"] in (None, "tv")]
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
    return jsonify(store.templates_payload(get_database()))


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
        return jsonify(store.set_signup_template(get_database(), _body().get("template_id")))
    except store.PermissionRequestError as exc:
        return _err(exc)


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
        _audit("permissions_profile", target_id=profile_id, target_name=prof.get("name"),
               detail={"template_id": body.get("template_id"), "set": body.get("set"),
                       "reset": body.get("reset"), "reset_categories": body.get("reset_categories")})
        return jsonify(out)
    except store.PermissionRequestError as exc:
        return _err(exc)
    except store.PermissionsUnavailable as exc:
        return _unavailable(exc)


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
