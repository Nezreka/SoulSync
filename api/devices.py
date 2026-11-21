"""listen anywhere, the server side of pairing (PAIRING_SPEC.md): this
server's identity, paired devices, invite codes and the address list.

admin routes are @admin_only. the routes a paired device calls carry a
device mark (core.connect.device_access) as well as their inventory mark.
"""

from __future__ import annotations

import time

from flask import Blueprint, g, jsonify, request

from core.connect.device_access import device_pick
from core.permissions import device_only
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.devices")

bp = Blueprint("devices", __name__)

get_database = None
config_manager = None
_require_login_enabled = lambda: False  # noqa: E731 - injected


def configure(*, get_database, config_manager, require_login_enabled=None):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager
    if require_login_enabled is not None:
        globals()["_require_login_enabled"] = require_login_enabled


def create_blueprint():
    return bp


def _identity():
    from core.connect.server_key import server_identity
    return server_identity(get_database(), config_manager)


# ── this server ──────────────────────────────────────────────────────────

@bp.route("/api/devices/identity", methods=["GET"])
@admin_only
def server_identity_view():
    """the server fingerprint for Settings: lookup ID, fingerprint and
    public key. never the secret."""
    from core.connect.server_key import public_view
    try:
        return jsonify({"success": True, **public_view(_identity())})
    except Exception as exc:  # noqa: BLE001 - a damaged key file says so
        logger.error("server identity unavailable: %s", exc)
        return jsonify({"success": False, "error": "The server identity could not be read. "
                        "Check the server_key file next to the database."}), 500


# ── paired devices (admin) ───────────────────────────────────────────────

def _audit(action, detail=None, target_id=None, target_name=None):
    try:
        from api.profile_admin import audit
        audit(action, target_id, target_name, detail)
    except Exception:  # noqa: BLE001 - the log never fails the action
        logger.debug("device audit failed", exc_info=True)


@bp.route("/api/devices", methods=["GET"])
@admin_only
def list_devices():
    """every paired device, and the ones still waiting for their pick."""
    from core.connect import device_tokens as T
    now = time.time()
    rows = [T.public_row(r, now) for r in T.list_devices(get_database(), now=now)]
    return jsonify({"success": True, "devices": rows})


@bp.route("/api/devices/<int:device_id>", methods=["PATCH"])
@admin_only
def rename_device(device_id):
    """{name}: the name the device list shows."""
    from core.connect import device_tokens as T
    name = str((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Give it a name"}), 400
    if not T.rename(get_database(), device_id, name):
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True})


@bp.route("/api/devices/<int:device_id>", methods=["DELETE"])
@admin_only
def revoke_device(device_id):
    """unpair one device, at once: its next request is refused."""
    from core.connect import device_tokens as T
    db = get_database()
    row = T.get(db, device_id)
    if not row or not T.revoke(db, device_id):
        return jsonify({"success": False, "error": "Not found or already unpaired"}), 404
    _audit("device_unpaired", detail=row.get("name"), target_id=row.get("profile_id"))
    return jsonify({"success": True})


@bp.route("/api/devices/profiles/<int:profile_id>/revoke", methods=["POST"])
@admin_only
def revoke_profile_devices(profile_id):
    """unpair every device of one profile."""
    from core.connect import device_tokens as T
    db = get_database()
    n = T.revoke_for_profile(db, profile_id)
    if n:
        _audit("devices_unpaired", detail=f"{n} device(s)", target_id=profile_id,
               target_name=(db.get_profile(profile_id) or {}).get("name"))
    return jsonify({"success": True, "revoked": n})


# ── the device itself ────────────────────────────────────────────────────

def _device():
    """this request's device row (set by web_server's device gate), or None."""
    dev = g.get("device_auth") and g.get("device")
    return dev or None


def _no_device():
    return jsonify({"success": False, "error": "device_token_required",
                    "message": "This is for paired devices."}), 401


@bp.route("/api/devices/me", methods=["GET"])
@device_only
@device_pick
def device_me():
    """who this device is: its name, whether it has picked a profile (and
    how long it has left to), and the server it is paired with."""
    dev = _device()
    if dev is None:
        return _no_device()
    from core.connect import device_tokens as T
    from core.connect.server_key import public_view
    db = get_database()
    row = T.get(db, dev["id"]) or {}
    out = T.public_row(row)
    out.pop("last_ip", None)
    return jsonify({"success": True, "device": out, "server": public_view(_identity())})


@bp.route("/api/devices/me/profiles", methods=["GET"])
@device_only
@device_pick
def device_pick_list():
    """the profiles this device may pick, and what each asks for."""
    dev = _device()
    if dev is None:
        return _no_device()
    if dev.get("profile_id") is not None:
        return jsonify({"success": False, "error": "device_already_picked",
                        "message": "This device already has its profile."}), 409
    from core.connect.pick import pick_list
    profiles = pick_list(get_database(), dev.get("kind") or "", login_mode=bool(_require_login_enabled()))
    return jsonify({"success": True, "profiles": profiles})


@bp.route("/api/devices/me/profile", methods=["POST"])
@device_only
@device_pick
def device_pick_profile():
    """{profile_id, pin?, password?}: the pick. the same checks as the
    profile picker (api.user_profiles.check_profile_pick), and a pinned
    profile always asks for its pin here. once only."""
    dev = _device()
    if dev is None:
        return _no_device()
    if dev.get("profile_id") is not None:
        return jsonify({"success": False, "error": "device_already_picked",
                        "message": "This device already has its profile."}), 409
    body = request.get_json(silent=True) or {}
    try:
        profile_id = int(body.get("profile_id") or 0)
    except (TypeError, ValueError):
        profile_id = 0
    db = get_database()
    profile = db.get_profile(profile_id) if profile_id else None
    from core.connect.pick import may_use
    # a profile that isn't on this device's list reads as not there at all
    if not profile or not may_use(db, profile, dev.get("kind") or ""):
        return jsonify({"success": False, "error": "Profile not found"}), 404
    from api.user_profiles import check_profile_pick
    refused, _pin_checked = check_profile_pick(
        db, profile, pin=str(body.get("pin") or ""), password=str(body.get("password") or ""),
        ip=request.remote_addr or "unknown", now=time.time(),
        is_new_sign_in=True, always_ask_pin=True)
    if refused is not None:
        return refused
    from core.connect import device_tokens as T
    if not T.pick_profile(db, dev["id"], profile_id):
        return jsonify({"success": False, "error": "device_pick_expired",
                        "message": "No profile was picked in time. Pair this device again."}), 409
    _audit("device_paired", detail=dev.get("name"), target_id=profile_id, target_name=profile.get("name"))
    return jsonify({"success": True, "profile": {"id": profile_id, "name": profile.get("name")}})
