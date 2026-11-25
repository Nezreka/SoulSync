"""listen anywhere, the server side of pairing (PAIRING_SPEC.md): this
server's identity, paired devices, invite codes and the address list.

admin routes are @admin_only. the routes a paired device calls carry a
device mark (core.connect.device_access) as well as their inventory mark.
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, g, jsonify, request

from core.connect.device_access import device_pick, device_playback
from core.permissions import device_only, public
from core.security.rate_limit import AttemptLimiter
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
    try:  # the rendezvous is best-effort: it self-gates on having a certificate
        from core.connect import rendezvous

        rendezvous.start(get_database, config_manager)
    except Exception:  # noqa: BLE001 - pairing must never break app setup
        logger.debug("connect rendezvous start failed", exc_info=True)


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


def _pairing(code):
    """the pairing payload for a code: what the QR carries. the addresses
    are this server's, home ones first, the browser's own among them."""
    from core.connect.addresses import pairing_payload, payload_text, warnings
    ident = _identity()
    addrs = _addresses()
    payload = pairing_payload(lookup_id=ident.lookup_id, fingerprint=ident.fingerprint,
                              addresses=addrs, code=code)
    return {"payload": payload, "payload_text": payload_text(payload), "warnings": warnings(addrs)}


# ── paired devices (admin) ───────────────────────────────────────────────

def _audit(action, detail=None, target_id=None, target_name=None, actor=None):
    try:
        from api.profile_admin import audit
        audit(action, target_id, target_name, detail, actor=actor)
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
    _drop_app_passwords(db, [device_id])
    _audit("device_unpaired", detail=row.get("name"), target_id=row.get("profile_id"))
    return jsonify({"success": True})


@bp.route("/api/devices/profiles/<int:profile_id>/revoke", methods=["POST"])
@admin_only
def revoke_profile_devices(profile_id):
    """unpair every device of one profile."""
    from core.connect import device_tokens as T
    db = get_database()
    ids = [d["id"] for d in T.list_devices(db) if d.get("profile_id") == profile_id]
    n = T.revoke_for_profile(db, profile_id)
    _drop_app_passwords(db, ids)
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
    from core.connect import rendezvous as R
    from core.connect.server_key import public_view
    db = get_database()
    row = T.get(db, dev["id"]) or {}
    out = T.public_row(row)
    out.pop("last_ip", None)
    profile_info = None
    pid = row.get("profile_id")
    if pid is not None:
        try:
            prof = db.get_profile(int(pid))
            if prof:
                profile_info = {
                    "id": int(prof["id"]),
                    "name": prof.get("name") or "",
                    "avatar_color": prof.get("avatar_color"),
                    "avatar_url": prof.get("avatar_url"),
                }
        except Exception:  # noqa: BLE001
            profile_info = None
    return jsonify({
        "success": True,
        "device": out,
        "profile": profile_info,
        "server": public_view(_identity()),
        "addresses": R.current_addresses(get_database, config_manager),
    })


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


@bp.route("/api/devices/me/switch-profiles", methods=["GET"])
@device_only
@device_playback
def device_switch_profiles():
    """List household profiles a live paired device may switch to (with PIN/password requirements)."""
    dev = _device()
    if dev is None:
        return _no_device()
    from core.connect.pick import pick_list
    db = get_database()
    current_pid = dev.get("profile_id")
    profiles = pick_list(db, dev.get("kind") or "", login_mode=bool(_require_login_enabled()))
    for p in profiles:
        p["active"] = (p.get("id") == current_pid)
    return jsonify({"success": True, "profiles": profiles})


@bp.route("/api/devices/me/switch-profile", methods=["POST"])
@device_only
@device_playback
def device_switch_profile():
    """Switch a live paired device to another household profile (enforces PIN/password via check_profile_pick)."""
    dev = _device()
    if dev is None:
        return _no_device()
    body = request.get_json(silent=True) or {}
    try:
        profile_id = int(body.get("profile_id") or 0)
    except (TypeError, ValueError):
        profile_id = 0
    db = get_database()
    profile = db.get_profile(profile_id) if profile_id else None
    from core.connect.pick import may_use
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
    if not T.switch_profile(db, dev["id"], profile_id):
        return jsonify({"success": False, "error": "switch_failed",
                        "message": "Could not switch profile on this device."}), 409
    return jsonify({
        "success": True,
        "profile": {
            "id": profile_id,
            "name": profile.get("name"),
            "avatar_color": profile.get("avatar_color"),
            "avatar_url": profile.get("avatar_url"),
        },
    })


# ── invite codes (admin) ─────────────────────────────────────────────────

# failed redemptions per address: 10 a minute (PAIRING_SPEC.md 2)
_redeem_limiter = AttemptLimiter(max_attempts=10, window_seconds=60)


def _code_db_secret():
    return get_database(), _identity().key


@bp.route("/api/devices/codes", methods=["GET"])
@admin_only
def list_codes():
    """codes still waiting to be used. never the codes themselves."""
    from core.connect import invite_codes as C
    return jsonify({"success": True, "codes": C.live_codes(get_database())})


@bp.route("/api/devices/codes", methods=["POST"])
@admin_only
def create_code():
    """{note?} -> the code, shown this once, with its pairing payload."""
    from core.connect import invite_codes as C
    from core.profile_context import get_current_profile_id
    db, secret = _code_db_secret()
    note = str((request.get_json(silent=True) or {}).get("note") or "")
    try:
        code, row = C.create(db, secret, created_by=get_current_profile_id(), note=note)
    except C.TooManyLive:
        return jsonify({"success": False, "error": f"{C.MAX_LIVE} codes are already waiting. "
                        "Cancel one or let it run out."}), 409
    C.sweep(db)
    try:  # tell the rendezvous, when it is running, so the code is live there too
        from core.connect import rendezvous

        rendezvous.register_created(row["code_hash"], int(row["seconds_left"]))
    except Exception:  # noqa: BLE001
        logger.debug("connect rendezvous register failed", exc_info=True)
    _audit("device_code_created", detail=note.strip()[:80] or None)
    out = {"success": True, "code": C.display(code), **row}
    out.update(_pairing(C.display(code)))
    return jsonify(out), 201


@bp.route("/api/devices/codes/<int:code_id>", methods=["DELETE"])
@admin_only
def cancel_code(code_id):
    from core.connect import invite_codes as C
    if not C.cancel(get_database(), code_id):
        return jsonify({"success": False, "error": "Not found, used or already cancelled"}), 404
    return jsonify({"success": True})


# ── redemption (the app, with its code) ──────────────────────────────────

def _secure_enough() -> bool:
    """https, or plain http from this machine or the home network. a code
    or a token never crosses the internet in the clear."""
    from core.connect.addresses import is_home_address
    if request.is_secure:
        return True
    ip = request.remote_addr or ""
    return ip.startswith("127.") or ip == "::1" or is_home_address(ip)


@bp.route("/api/devices/redeem", methods=["POST"])
@public
def redeem_code():
    """{code, name, platform?, app?} -> a device token, shown this once.
    the code is burnt and the device starts unprofiled: it picks its
    profile next, within 15 minutes. every failure reads the same."""
    from core.connect import device_tokens as T
    from core.connect import invite_codes as C
    from core.connect.server_key import public_view
    if not _secure_enough():
        return jsonify({"success": False, "error": "https_required",
                        "message": "Pair over https, or from your home network."}), 403
    ip = request.remote_addr or "unknown"
    now = time.time()
    locked, retry = _redeem_limiter.is_locked(ip, now)
    if locked:
        return (jsonify({"success": False, "error": "rate_limited",
                         "message": "Too many wrong codes. Wait a minute and try again.",
                         "retry_after_seconds": retry}), 429, {"Retry-After": str(retry)})
    body = request.get_json(silent=True) or {}
    db, secret = _code_db_secret()
    outcome, code_id = C.redeem(db, secret, body.get("code"), now=now)
    if outcome != C.REDEEMED:
        _redeem_limiter.record_failure(ip, now)
        return jsonify({"success": False, "error": "unknown_or_expired_code",
                        "message": "That code isn't valid. Ask for a new one."}), 404
    app_name = str(body.get("app") or "").strip()
    kind = T.KIND_PLAYER if app_name.lower() == T.KIND_PLAYER else T.KIND_MUSIC
    token, device_id = T.create(db, name=str(body.get("name") or ""), platform=str(body.get("platform") or ""),
                                app=app_name, kind=kind, ip=ip, invite_code_id=code_id, now=now)
    C.mark_device(db, code_id, device_id)
    try:  # the service drops the registration the moment a code is redeemed
        from core.connect import rendezvous

        normalized = C.normalize(body.get("code"))
        if normalized:
            rendezvous.report_redeemed(C.code_hash(normalized))
    except Exception:  # noqa: BLE001
        logger.debug("connect rendezvous redeemed report failed", exc_info=True)
    T.sweep_expired(db, now=now)
    _audit("device_code_used", detail=str(body.get("name") or "")[:80] or None,
           actor=(None, "a device", False))
    return jsonify({"success": True, "token": token, "device_id": device_id,
                    "pick_seconds": T.PICK_WINDOW, "server": public_view(_identity())}), 201


# ── addresses and the reachability check ─────────────────────────────────

def _addresses():
    from core.connect.addresses import address_list
    return address_list(config_manager.get, request_host_url=request.host_url)


def _server_payload():
    from core.connect import reachability as R
    from core.connect import rendezvous as RV
    from core.connect.addresses import configured, warnings
    from core.connect.server_key import public_view
    addrs = _addresses()
    conf = configured(config_manager.get)
    return {**public_view(_identity()), "addresses": addrs, **conf, "warnings": warnings(addrs),
            "probe": _probe_view(R.latest(), conf["remote_url"]), "guidance": R.GUIDANCE,
            "upnp": RV.upnp_status(config_manager)}


def _probe_view(probe, remote_url):
    if not probe:
        return None
    return {**probe, "url": (remote_url.rstrip("/") + probe["path"]) if remote_url else None}


@bp.route("/api/devices/server", methods=["GET"])
@admin_only
def server_view():
    """this server for Settings > Devices: fingerprint, addresses (local
    first), the saved remote and extra addresses, plain-http warnings, the
    last reachability probe and the guidance for getting in from outside."""
    return jsonify({"success": True, **_server_payload()})


@bp.route("/api/devices/addresses", methods=["PUT"])
@admin_only
def save_addresses():
    """{remote_url, custom_urls}: blank remote clears it."""
    from core.connect.addresses import CUSTOM_KEY, MAX_CUSTOM, REMOTE_KEY, clean_url
    body = request.get_json(silent=True) or {}
    remote, why = clean_url(body.get("remote_url"))
    if why:
        return jsonify({"success": False, "error": f"Remote address: {why}"}), 400
    raw = body.get("custom_urls") or []
    if not isinstance(raw, list):
        return jsonify({"success": False, "error": "Extra addresses: send a list"}), 400
    custom = []
    for item in raw:
        url, why = clean_url(item)
        if why:
            return jsonify({"success": False, "error": f"{item}: {why}"}), 400
        if url and url not in custom and url != remote:
            custom.append(url)
    if len(custom) > MAX_CUSTOM:
        return jsonify({"success": False, "error": f"At most {MAX_CUSTOM} extra addresses"}), 400
    config_manager.set(REMOTE_KEY, remote or "")
    config_manager.set(CUSTOM_KEY, custom)
    return jsonify({"success": True, **_server_payload()})


@bp.route("/api/devices/upnp", methods=["PUT"])
@admin_only
def save_upnp():
    """{enabled: bool}: toggle automatic UPnP IGD / NAT-PMP port forwarding."""
    from core.connect import rendezvous as RV
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))
    RV.set_upnp_enabled(enabled, get_database, config_manager)
    return jsonify({"success": True, **_server_payload()})


@bp.route("/api/devices/reachability", methods=["POST"])
@admin_only
def check_reachability():
    """a new one-time probe, and the server fetching its own remote address
    with it. result is None when no remote address is saved."""
    from core.connect import reachability as R
    from core.connect.addresses import configured
    remote = configured(config_manager.get)["remote_url"]
    probe = R.start()
    result = R.self_check(remote, probe["nonce"]) if remote else None
    probe = R.view(probe["nonce"]) or probe
    return jsonify({"success": True, "result": result, "probe": _probe_view(probe, remote),
                    "guidance": R.GUIDANCE})


@bp.route("/api/devices/reachability", methods=["GET"])
@admin_only
def reachability_status():
    """the newest probe and who has fetched it (a phone on mobile data)."""
    from core.connect import reachability as R
    from core.connect.addresses import configured
    return jsonify({"success": True,
                    "probe": _probe_view(R.latest(), configured(config_manager.get)["remote_url"])})


@bp.route("/api/devices/reachability/<nonce>", methods=["GET"])
@public
def reachability_probe(nonce):
    """the one-time URL. the nonce is the only thing it gives back, so it
    proves this very server answered and tells nobody anything else."""
    from core.connect import reachability as R
    known = R.record_hit(nonce, request.remote_addr or "",
                         self_marker=request.headers.get(R.SELF_HEADER, ""))
    if not known:
        return jsonify({"success": False, "error": "unknown_probe"}), 404
    if "text/html" in (request.headers.get("Accept") or ""):
        return ("<!doctype html><meta name=viewport content='width=device-width'>"
                "<title>SoulSync</title><p style='font:18px system-ui;padding:24px'>"
                "SoulSync answered. You can close this page.</p>")
    return jsonify({"success": True, "soulsync_probe": nonce})


# ── music apps (admin): the Subsonic api's app passwords and settings ────

def _drop_app_passwords(db, device_ids):
    """an unpaired music app's password goes with it."""
    try:
        from core.subsonic import store as S
        for did in device_ids:
            S.drop_app_password(db, did)
    except Exception:  # noqa: BLE001 - the device is already refused; this is tidying
        logger.debug("dropping app passwords failed", exc_info=True)


@bp.route("/api/devices/music-apps", methods=["GET"])
@admin_only
def music_apps_view():
    """for "Set up a music app": the profiles that may use one, this
    server's addresses, the media server switches and the transcoder."""
    from core.connect.pick import pick_list
    from core.subsonic import bridge as B
    from core.subsonic import transcode as X
    db = get_database()
    profiles = [{"id": p["id"], "name": p["name"]} for p in pick_list(db, "music", login_mode=False)]
    active = None
    try:
        active = config_manager.get_active_media_server()
    except Exception:  # noqa: BLE001
        active = None
    servers = [{"id": s, "name": B.SOURCE_NAMES[s], "on": B.enabled(config_manager.get, s),
                "active": s == active} for s in B.SOURCES]
    return jsonify({"success": True, "profiles": profiles, "addresses": _addresses(),
                    "bridge": servers, "transcode_format": str(config_manager.get("music_apps.transcode_format",
                                                                                   "mp3") or "mp3"),
                    "transcode_slots": X.clamp_jobs(config_manager.get("music_apps.transcode_slots",
                                                                       X.MAX_JOBS)),
                    "slots_range": [X.MIN_JOBS, X.MOST_JOBS],
                    "formats": sorted(X.FORMATS), "ffmpeg": bool(X.ffmpeg())})


@bp.route("/api/devices/music-apps", methods=["POST"])
@admin_only
def set_up_music_app():
    """{profile_id, name?} -> a music app, paired to that profile at once:
    the server address, the username, an app password and an api key, all
    shown this once (and in the QR). the app shows up under Paired devices
    and is unpaired from there."""
    from core.connect import device_tokens as T
    from core.connect.addresses import payload_text
    from core.connect.pick import may_use
    from core.subsonic import store as S
    body = request.get_json(silent=True) or {}
    try:
        profile_id = int(body.get("profile_id") or 0)
    except (TypeError, ValueError):
        profile_id = 0
    db = get_database()
    profile = db.get_profile(profile_id) if profile_id else None
    if not profile or not may_use(db, profile, T.KIND_MUSIC):
        return jsonify({"success": False, "error": "Pick a profile that may use music apps"}), 400
    name = str(body.get("name") or "").strip()[:80] or "Music app"
    token, device_id = T.create(db, name=name, platform="", app="Music app", kind=T.KIND_MUSIC,
                                ip=request.remote_addr or "")
    if not T.pick_profile(db, device_id, profile_id):
        T.revoke(db, device_id)
        return jsonify({"success": False, "error": "Couldn't set the app up. Try again."}), 500
    password = S.new_app_password()
    S.save_app_password(db, device_id, profile_id, password, config_manager._encrypt_value)
    addresses = _addresses()
    server = next((a["url"] for a in addresses if a["kind"] == "remote"), None) or (
        addresses[0]["url"] if addresses else request.host_url.rstrip("/"))
    username = profile.get("name") or ""
    qr = payload_text({"type": "soulsync-music-app", "server": server, "username": username,
                       "password": password, "apiKey": token})
    _audit("music_app_set_up", detail=name, target_id=profile_id, target_name=username)
    return jsonify({"success": True, "device_id": device_id, "name": name, "server": server,
                    "addresses": addresses, "username": username, "password": password,
                    "api_key": token, "qr_text": qr}), 201


@bp.route("/api/devices/music-apps/settings", methods=["PUT"])
@admin_only
def save_music_app_settings():
    """{bridge: {plex|jellyfin|navidrome: bool}, transcode_format?, transcode_slots?}"""
    from core.subsonic import bridge as B
    from core.subsonic import transcode as X
    body = request.get_json(silent=True) or {}
    bridge = body.get("bridge") or {}
    if not isinstance(bridge, dict):
        return jsonify({"success": False, "error": "bridge: send an object"}), 400
    for source, on in bridge.items():
        if source not in B.SOURCES:
            return jsonify({"success": False, "error": f"Unknown media server: {source}"}), 400
        config_manager.set(B.setting_key(source), bool(on))
    fmt = body.get("transcode_format")
    if fmt is not None:
        if fmt not in X.FORMATS:
            return jsonify({"success": False, "error": "Pick mp3 or opus"}), 400
        config_manager.set("music_apps.transcode_format", fmt)
    slots = body.get("transcode_slots")
    if slots is not None:
        if isinstance(slots, bool) or not isinstance(slots, int) or not X.MIN_JOBS <= slots <= X.MOST_JOBS:
            return jsonify({"success": False,
                            "error": f"Songs converted at once: pick {X.MIN_JOBS} to {X.MOST_JOBS}"}), 400
        config_manager.set("music_apps.transcode_slots", slots)
    _audit("music_apps_settings", detail=json.dumps({"bridge": bridge, "format": fmt, "slots": slots},
                                                    sort_keys=True)[:200])
    return music_apps_view()
