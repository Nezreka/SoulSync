"""profile housekeeping: the admin audit log, "sign out everywhere", invite
links and avatar images (sept 25 2026).

identity is the session's (core.profile_context); who may touch whom is
core.permissions.may_manage_profile, same as the rest of the profile api.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, send_file, session

from core.permissions import may_manage_profile
from core.profile_context import admin_only, get_current_profile_id
from core.security.rate_limit import TargetedLimiter
from utils.logging_config import get_logger

logger = get_logger("api.profile_admin")

bp = Blueprint("profile_admin", __name__)

get_database = None
config_manager = None
_require_login_enabled = lambda: False  # noqa: E731 - injected

# invite tokens are 24 random bytes, unguessable; the limiter is for the
# endpoint's sake, not the token's
_invite_limiter = TargetedLimiter(max_attempts=20, window_seconds=300, max_client_attempts=40)

AVATAR_MAX_BYTES = 3 * 1024 * 1024
AVATAR_SIDE = 512


def configure(*, get_database, config_manager, require_login_enabled):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager
    globals()["_require_login_enabled"] = require_login_enabled


def create_blueprint():
    return bp


def _actor():
    pid = get_current_profile_id()
    row = get_database().get_profile(pid) if pid is not None else None
    return pid, (row or {}).get("name") or "", bool(row and (row.get("is_admin") or int(pid) == 1))


def audit(action, target_id=None, target_name=None, detail=None, actor=None):
    """one line in the admin log. never fails the action it records."""
    try:
        pid, name, _ = actor or _actor()
        get_database().add_profile_audit(actor_id=pid, actor_name=name, action=action,
                                         target_id=target_id, target_name=target_name, detail=detail)
    except Exception:  # noqa: BLE001
        logger.debug("audit write failed", exc_info=True)


# ── audit log ────────────────────────────────────────────────────────────

@bp.route("/api/profiles/audit", methods=["GET"])
@admin_only
def list_audit():
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        limit, offset = 100, 0
    return jsonify({"success": True, "entries": get_database().list_profile_audit(limit, offset)})


# ── sign out everywhere ──────────────────────────────────────────────────

@bp.route("/api/profiles/<int:profile_id>/sign-out-everywhere", methods=["POST"])
def sign_out_everywhere(profile_id):
    """every browser signed in as this profile loses it. yourself: this
    browser stays signed in. someone else: admin only (profile 1 by itself)."""
    db = get_database()
    pid, name, is_admin = _actor()
    if pid is None or not may_manage_profile(pid, is_admin, profile_id):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    target = db.get_profile(profile_id)
    if not target:
        return jsonify({"success": False, "error": "Profile not found"}), 404
    epoch = db.bump_profile_session_epoch(profile_id)
    if epoch is None:
        return jsonify({"success": False, "error": "Could not sign out"}), 500
    from core.security.session_epoch import forget
    forget(profile_id)
    if int(profile_id) == int(pid):
        session["profile_epoch"] = epoch
    audit("signed_out_everywhere", profile_id, target.get("name"), actor=(pid, name, is_admin))
    return jsonify({"success": True})


# ── invites ──────────────────────────────────────────────────────────────

_PRESET_KEYS = ("allowed_sides", "can_download", "allowed_pages", "hide_explicit", "max_rating",
                "request_limit", "request_limit_days")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_sql(delta_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _clean_preset(raw):
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k in _PRESET_KEYS:
        if k in raw:
            out[k] = raw[k]
    if out.get("allowed_sides") not in (None, "music", "video", "both"):
        out.pop("allowed_sides")
    if out.get("max_rating") not in (None, "G", "PG", "PG-13", "R"):
        out.pop("max_rating")
    return out


@bp.route("/api/profiles/invites", methods=["GET"])
@admin_only
def list_invites():
    now = _now_sql()
    rows = get_database().list_profile_invites()
    for r in rows:
        r["state"] = ("used" if r.get("used_at") else "revoked" if r.get("revoked_at")
                      else "expired" if str(r.get("expires_at") or "") <= now else "open")
    return jsonify({"success": True, "invites": rows})


@bp.route("/api/profiles/invites", methods=["POST"])
@admin_only
def create_invite():
    """{preset, note?, expires_hours?} -> {token, path}. the token is shown
    once; only its hash is stored."""
    body = request.get_json(silent=True) or {}
    try:
        hours = max(1, min(720, int(body.get("expires_hours") or 72)))
    except (TypeError, ValueError):
        hours = 72
    token = secrets.token_urlsafe(24)
    pid, name, is_admin = _actor()
    iid = get_database().create_profile_invite(
        token_hash=_hash(token), created_by=pid, preset=_clean_preset(body.get("preset")),
        note=(str(body.get("note") or "").strip()[:200] or None), expires_at=_now_sql(hours))
    if not iid:
        return jsonify({"success": False, "error": "Could not create the invite"}), 500
    audit("invite_created", detail=f"expires in {hours}h", actor=(pid, name, is_admin))
    return jsonify({"success": True, "id": iid, "token": token, "path": f"/invite/{token}",
                    "expires_hours": hours}), 201


@bp.route("/api/profiles/invites/<int:invite_id>", methods=["DELETE"])
@admin_only
def revoke_invite(invite_id):
    ok = get_database().revoke_profile_invite(invite_id)
    if ok:
        audit("invite_revoked", detail=f"invite {invite_id}")
    return (jsonify({"success": True}) if ok else
            (jsonify({"success": False, "error": "Not found, used or already revoked"}), 404))


def _open_invite(token):
    """(invite, error_response). counts every lookup against the limiter."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    locked, retry = _invite_limiter.is_locked(ip, "invite", now)
    if locked:
        return None, (jsonify({"success": False, "error": "Too many attempts"}), 429, {"Retry-After": str(retry)})
    inv = get_database().get_profile_invite_by_hash(_hash(token or ""))
    if (not inv or inv.get("used_at") or inv.get("revoked_at")
            or str(inv.get("expires_at") or "") <= _now_sql()):
        _invite_limiter.record_failure(ip, "invite", now)
        return None, (jsonify({"success": False, "error": "This invite link isn't valid any more"}), 404)
    return inv, None


@bp.route("/api/invite/<token>", methods=["GET"])
def read_invite(token):
    """what the invite page shows before someone accepts: nothing private."""
    inv, err = _open_invite(token)
    if err:
        return err
    preset = inv.get("preset") or {}
    return jsonify({"success": True, "note": inv.get("note"), "expires_at": inv.get("expires_at"),
                    "sides": preset.get("allowed_sides") or "music",
                    "can_download": bool(preset.get("can_download", True)),
                    "password_required": bool(_require_login_enabled())})


@bp.route("/api/invite/<token>/accept", methods=["POST"])
def accept_invite(token):
    """{name, avatar_color?, pin?, password?}: makes the profile with the
    invite's access and signs this browser in as it. the invite is spent
    before the profile exists, so two tabs can't both use it."""
    inv, err = _open_invite(token)
    if err:
        return err
    db = get_database()
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:40]
    if not name:
        return jsonify({"success": False, "error": "Pick a name"}), 400
    if db.get_profile_by_name(name):
        return jsonify({"success": False, "error": "That name is taken"}), 409
    pin = str(body.get("pin") or "")
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 20):
        return jsonify({"success": False, "error": "A PIN is 4 to 20 digits"}), 400
    password = str(body.get("password") or "")
    if _require_login_enabled() and len(password) < 6:
        return jsonify({"success": False, "error": "Choose a password of at least 6 characters"}), 400
    if not db.claim_profile_invite(inv["id"], 0):
        return jsonify({"success": False, "error": "This invite link isn't valid any more"}), 404
    preset = inv.get("preset") or {}
    pin_hash = None
    if pin:
        from werkzeug.security import generate_password_hash
        pin_hash = generate_password_hash(pin, method="pbkdf2:sha256")
    color = str(body.get("avatar_color") or "#6366f1")[:20]
    new_id = db.create_profile(name, color, pin_hash, is_admin=False,
                               allowed_pages=preset.get("allowed_pages"),
                               can_download=bool(preset.get("can_download", True)),
                               allowed_sides=preset.get("allowed_sides"))
    if not new_id:
        return jsonify({"success": False, "error": "Could not create the profile"}), 500
    extra = {k: preset[k] for k in ("hide_explicit", "max_rating", "request_limit", "request_limit_days")
             if k in preset}
    if extra:
        db.update_profile(new_id, **extra)
    if password:
        db.set_profile_password(new_id, password)
    # record who used it (claimed with 0 first so the spend came before the create)
    try:
        conn = db._get_connection()
        conn.execute("UPDATE profile_invites SET used_by = ? WHERE id = ?", (new_id, inv["id"]))
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001
        logger.debug("invite used_by write failed", exc_info=True)
    session["profile_id"] = new_id
    session["profile_epoch"] = 0
    if _require_login_enabled():
        session["login_authenticated"] = True
    # an admin minted this link: it opens the launch lock for the person it
    # was made for, or the invite would lead to a locked door
    session["launch_pin_verified"] = True
    audit("invite_used", new_id, name, actor=(new_id, name, False))
    return jsonify({"success": True, "profile_id": new_id}), 201


# ── avatars ──────────────────────────────────────────────────────────────

def _avatar_dir() -> str:
    d = os.environ.get("SOULSYNC_AVATAR_DIR")
    if not d:
        base = getattr(config_manager, "base_dir", None)
        d = os.path.join(str(base) if base else ".", "storage", "avatars")
    os.makedirs(d, exist_ok=True)
    return d


def _avatar_path(profile_id: int) -> str:
    return os.path.join(_avatar_dir(), f"profile-{int(profile_id)}.webp")


@bp.route("/api/profiles/<int:profile_id>/avatar", methods=["POST"])
def upload_avatar(profile_id):
    """multipart 'file': any image pillow reads, re-encoded to a 512px webp
    (so nothing but pixels is kept: no metadata, no svg, no scripts)."""
    db = get_database()
    pid, name, is_admin = _actor()
    if pid is None or not may_manage_profile(pid, is_admin, profile_id):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    if not db.get_profile(profile_id):
        return jsonify({"success": False, "error": "Profile not found"}), 404
    f = request.files.get("file")
    if not f:
        return jsonify({"success": False, "error": "No image"}), 400
    raw = f.read(AVATAR_MAX_BYTES + 1)
    if len(raw) > AVATAR_MAX_BYTES:
        return jsonify({"success": False, "error": "That image is over 3 MB"}), 413
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
        img = ImageOps.fit(img, (AVATAR_SIDE, AVATAR_SIDE), method=Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "WEBP", quality=88)
    except Exception:  # noqa: BLE001
        return jsonify({"success": False, "error": "That file isn't an image we can read"}), 400
    path = _avatar_path(profile_id)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(out.getvalue())
    os.replace(tmp, path)
    url = f"/api/profiles/{int(profile_id)}/avatar?v={int(time.time())}"
    db.update_profile(profile_id, avatar_url=url)
    if int(profile_id) != int(pid):
        audit("avatar_changed", profile_id, (db.get_profile(profile_id) or {}).get("name"),
              actor=(pid, name, is_admin))
    return jsonify({"success": True, "avatar_url": url})


@bp.route("/api/profiles/<int:profile_id>/avatar", methods=["GET"])
def get_avatar(profile_id):
    path = _avatar_path(profile_id)
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "No avatar"}), 404
    resp = send_file(path, mimetype="image/webp", max_age=3600)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/api/profiles/<int:profile_id>/avatar", methods=["DELETE"])
def delete_avatar(profile_id):
    db = get_database()
    pid, name, is_admin = _actor()
    if pid is None or not may_manage_profile(pid, is_admin, profile_id):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        os.remove(_avatar_path(profile_id))
    except FileNotFoundError:
        pass
    db.update_profile(profile_id, avatar_url=None)
    return jsonify({"success": True})

