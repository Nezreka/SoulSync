"""sign in with oidc only (#1839).

an optional login-mode setting, ``security.oidc_only``. while it is on, oidc
is the one interactive way in: password sign-in, the recovery question,
sign in with plex, a password on an invite and switching profiles with a
password are all refused, and a session that didn't sign in with oidc ends.
api keys (/api/v1) are not interactive and are untouched.

turning it on needs proof that it will work for the person doing it: the
admin saving the setting must be in a session that signed in with oidc as
their own profile (the device row's oidc account is the profile's current
link), and every profile that can still be used must have an oidc link,
or it would be locked out the moment the setting saves.

offline recovery: ``SOULSYNC_OIDC_ONLY_OVERRIDE=off`` in the environment,
read once at start, turns the mode off while it is set. the saved setting
stays on, so taking the variable away (and restarting) brings it back. it
takes a restart and access to the host or the container, so it can't be
reached over the network. password hashes are kept while the mode is on,
so the admin signs in with the admin password while the override is set.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("security.oidc_only")

SETTING = "security.oidc_only"
OVERRIDE_ENV = "SOULSYNC_OIDC_ONLY_OVERRIDE"

# the settings status counts devices used this recently. a device row stays
# unrevoked when its session ends some other way, so an older one is noise
RECENT_DAYS = 7

# what every closed sign-in answers, word for word
REFUSED = "Sign-in is OIDC only on this server. Use the sign-in button for your provider."

_OFF_VALUES = ("off", "0", "false", "no")


def _read_override() -> bool:
    raw = (os.environ.get(OVERRIDE_ENV) or "").strip().lower()
    return raw in _OFF_VALUES


# read once, at start: changing it takes a restart, on purpose
_OVERRIDDEN = _read_override()


def overridden() -> bool:
    """the environment override is set (the mode is off whatever the setting says)"""
    return _OVERRIDDEN


def reload_override_for_tests() -> bool:
    global _OVERRIDDEN
    _OVERRIDDEN = _read_override()
    return _OVERRIDDEN


def stored_on(config_manager) -> bool:
    """what the setting says, before the override"""
    try:
        return bool(config_manager and config_manager.get(SETTING, False))
    except Exception:  # noqa: BLE001 - an unreadable setting is off
        return False


def is_on(config_manager) -> bool:
    """oidc is the only way in right now. it needs login mode; the override
    switches it off. a broken oidc configuration does NOT switch it off:
    the mode never opens the other doors by itself, the override does"""
    if _OVERRIDDEN or not stored_on(config_manager):
        return False
    try:
        return bool(config_manager.get("security.require_login", False))
    except Exception:  # noqa: BLE001
        return False


def log_start_state(config_manager) -> None:
    """called once at start. an override left set by accident is the risk,
    so it is said every time"""
    if _OVERRIDDEN:
        if stored_on(config_manager):
            logger.warning("%s is set: OIDC-only sign-in is OFF until it is removed and "
                           "SoulSync restarts. Password sign-in is open.", OVERRIDE_ENV)
        else:
            logger.warning("%s is set, but OIDC-only sign-in isn't on. Remove it.", OVERRIDE_ENV)


def status(config_manager, *, db=None, profiles=None) -> Dict[str, Any]:
    """what settings > security and the admin's indicator show"""
    out: Dict[str, Any] = {
        "stored": stored_on(config_manager),
        "on": is_on(config_manager),
        "overridden": overridden(),
        "override_env": OVERRIDE_ENV,
        "recent_days": RECENT_DAYS,
    }
    if db is not None:
        try:
            out["would_sign_out"] = int(db.count_live_devices_without_oidc(RECENT_DAYS))
        except Exception:  # noqa: BLE001
            out["would_sign_out"] = None
        try:
            from core.security.login_provisioning import profiles_without_oidc
            out["unlinked"] = profiles_without_oidc(
                profiles if profiles is not None else db.get_all_profiles(),
                db.profile_sign_in_methods)
        except Exception:  # noqa: BLE001
            out["unlinked"] = []
    return out


def session_signed_in_with_oidc(db, device_id: Optional[str], profile_id: Optional[int]) -> bool:
    """the proof the setting needs: this browser's sign-in was with oidc, as
    this profile, through the account the profile is linked to now, and the
    device hasn't been signed out"""
    if not device_id or profile_id is None:
        return False
    try:
        row = db.get_profile_device_oidc(device_id)
    except Exception:  # noqa: BLE001
        return False
    if not row or row.get("revoked") or not row.get("identity"):
        return False
    try:
        if int(row.get("owner_id")) != int(profile_id):
            return False
    except (TypeError, ValueError):
        return False
    return row.get("profile_identity") == row["identity"]


class _Overlay:
    """the configuration as it would be after a save: the posted security
    values over the stored ones. a secret posted blank or as the redaction
    marker means "unchanged", the same as config_manager.set treats it"""

    def __init__(self, config_manager, incoming: Dict[str, Any]):
        self._cm = config_manager
        self._in = incoming or {}

    def get(self, key: str, default: Any = None) -> Any:
        if key.startswith("security."):
            leaf = key[len("security."):]
            if leaf in self._in:
                value = self._in[leaf]
                if key == "security.oidc_client_secret":
                    marker = getattr(self._cm, "REDACTED_SENTINEL", object())
                    if value in (None, "") or value == marker:
                        return self._cm.get(key, default)
                return value
        return self._cm.get(key, default)


def check_security_change(config_manager, incoming: Dict[str, Any], db, *,
                          device_id: Optional[str], profile_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """the rules a save of the security settings has to pass, beyond the
    admin-password check login mode already had. None = fine, else
    {'error': ..., and 'stranded' or 'unlinked': [{'id', 'name'}]}.

    - turning login mode on: every member needs a way in (a password, or a
      plex or oidc link whose sign-in is on)
    - with login mode on, turning plex or oidc sign-in off, or blanking the
      oidc provider, must not leave a member with no way in
    - while oidc-only is on: oidc sign-in and login mode can't be turned
      off underneath it
    - turning oidc-only on: login mode on, oidc filled in and on, the saving
      session signed in with oidc as this admin, every usable profile linked
    """
    from core.security import oidc_signin
    from core.security.login_provisioning import (
        SignInWays, members_without_a_way_in, profiles_without_oidc)

    incoming = incoming if isinstance(incoming, dict) else {}
    after = _Overlay(config_manager, incoming)

    def _b(cm, key):
        try:
            return bool(cm.get(key, False))
        except Exception:  # noqa: BLE001
            return False

    login_before = _b(config_manager, "security.require_login")
    login_after = _b(after, "security.require_login")
    only_before = stored_on(config_manager)
    only_after = _b(after, SETTING)
    oidc_after = oidc_signin.read_settings(after) is not None

    if only_before and only_after:
        if not oidc_after:
            return {"error": "OIDC-only sign-in is on. Turn it off before turning off "
                             "Sign in with OIDC or clearing the provider."}
        if not login_after:
            return {"error": "OIDC-only sign-in is on. Turn it off before turning off login mode."}

    if only_after and not only_before:
        if not login_after:
            return {"error": "OIDC-only sign-in needs login mode on."}
        if not oidc_after:
            return {"error": "OIDC-only sign-in needs Sign in with OIDC turned on with a provider "
                             "filled in. Save that first."}
        if not session_signed_in_with_oidc(db, device_id, profile_id):
            return {"error": "Sign out and sign back in with OIDC first. OIDC-only can only be "
                             "turned on from a session that signed in with it."}
        unlinked = profiles_without_oidc(db.get_all_profiles(), db.profile_sign_in_methods)
        if unlinked:
            names = ", ".join(str(m.get("name") or "?") for m in unlinked)
            return {"error": f"These profiles have no OIDC connection and couldn't sign in: {names}. "
                             "Each needs to connect OIDC from My Account first.",
                    "unlinked": unlinked}

    if not login_after:
        return None
    ways_after = SignInWays.from_config(after, oidc_only=only_after and not overridden())
    ways_before = SignInWays.from_config(config_manager, oidc_only=is_on(config_manager))
    turning_on = not login_before
    narrower = (ways_before.plex and not ways_after.plex) or (ways_before.oidc and not ways_after.oidc)
    if turning_on or narrower:
        stranded = members_without_a_way_in(db.get_all_profiles(), db.profile_sign_in_methods, ways_after)
        if stranded:
            names = ", ".join(str(m.get("name") or "?") for m in stranded)
            if turning_on:
                msg = (f"These members couldn't sign in once login mode is on: {names}. "
                       "Give them a password in Manage Profiles, or a Plex or OIDC connection, first.")
            else:
                msg = (f"That would leave these members with no way to sign in: {names}. "
                       "Give them a password or another connection first.")
            return {"error": msg, "stranded": stranded, "members_without_password": stranded}
    return None


def refuse_unless_open(config_manager) -> Optional[tuple]:
    """for the routes oidc-only closes: a 403 to return, or None to carry on"""
    if not is_on(config_manager):
        return None
    from flask import jsonify
    return jsonify({"success": False, "error": REFUSED, "oidc_only": True}), 403


__all__ = [
    "SETTING", "OVERRIDE_ENV", "REFUSED", "overridden", "stored_on", "is_on", "status",
    "log_start_state", "session_signed_in_with_oidc", "check_security_change",
    "refuse_unless_open",
]
