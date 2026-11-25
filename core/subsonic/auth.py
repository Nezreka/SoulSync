"""who a music app request is (PAIRING_SPEC.md 5): always a paired device,
never an api key and never a profile's own password.

* ``apiKey=<device token>``: the token the device was given.
* ``u`` + ``t`` + ``s``: the salted md5 of that device's app password.
* ``u`` + ``p``: the app password itself, plain or ``enc:<hex>``.

``u`` is the profile's name. the answer is the device row; what that row
may do (revoked, unprofiled, music apps permission) is the device gate's
call (core.connect.device_access.decide), the same as for any device.

``resolve`` is pure apart from the two lookups it is handed, so the rules
are tested without a server.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from core.subsonic import response as R

# what resolve answers on a refusal: (subsonic error code, words)
Refusal = Tuple[int, str]


def credentials_present(params: Mapping[str, Any]) -> bool:
    return any(params.get(k) for k in ("apiKey", "u", "t", "s", "p"))


def _plain(p: str) -> str:
    if p.startswith("enc:"):
        try:
            return bytes.fromhex(p[4:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    return p


def password_matches(password: str, params: Mapping[str, Any]) -> bool:
    """does this app password match the request's t+s or p? constant time."""
    if not password:
        return False
    t, s, p = params.get("t") or "", params.get("s") or "", params.get("p") or ""
    if t and s:
        want = hashlib.md5((password + s).encode("utf-8")).hexdigest()  # noqa: S324 - the protocol's scheme
        return hmac.compare_digest(want, t.strip().lower())
    if p:
        return hmac.compare_digest(_plain(p).encode("utf-8"), password.encode("utf-8"))
    return False


def resolve(params: Mapping[str, Any], *,
            find_token: Callable[[str], Optional[Dict[str, Any]]],
            profiles_named: Callable[[str], List[int]],
            devices_for: Callable[[List[int]], List[Dict[str, Any]]],
            decrypt: Callable[[str], Optional[str]]) -> Tuple[Optional[Dict[str, Any]], Optional[Refusal]]:
    """(device row, None) or (None, (code, words))."""
    api_key = (params.get("apiKey") or "").strip()
    user = (params.get("u") or "").strip()
    if api_key:
        if user or params.get("t") or params.get("p"):
            return None, (R.CONFLICTING_AUTH, "Send the API key on its own, without a username or password.")
        row = find_token(api_key)
        if not row:
            return None, (R.INVALID_API_KEY, "That API key isn't valid. Set the app up again in SoulSync.")
        return row, None
    if not user:
        return None, (R.MISSING_PARAM, "Required parameter is missing: u")
    if not ((params.get("t") and params.get("s")) or params.get("p")):
        if params.get("t") or params.get("s"):
            return None, (R.MISSING_PARAM, "Required parameter is missing: %s" % ("s" if params.get("t") else "t"))
        return None, (R.MISSING_PARAM, "Required parameter is missing: p")
    pids = profiles_named(user)
    for row in devices_for(pids):
        try:
            password = decrypt(row.get("password_enc") or "")
        except Exception:  # noqa: BLE001 - an unreadable password matches nothing
            password = None
        if password and password_matches(password, params):
            row = dict(row)
            row.pop("password_enc", None)
            return row, None
    return None, (R.WRONG_CREDENTIALS, "Wrong username or password.")


def refusal_for(code: Optional[str]) -> Refusal:
    """the device gate's refusal (core.connect.device_access.decide) in
    subsonic words."""
    if code == "device_token_invalid":
        return R.INVALID_API_KEY, "This app isn't paired with this server any more. Set it up again in SoulSync."
    if code == "device_pick_expired":
        return R.INVALID_API_KEY, "No profile was picked in time. Set the app up again in SoulSync."
    if code == "device_pick_first":
        return R.NOT_AUTHORIZED, "Pick a profile for this app first."
    if code == "device_profile_not_allowed":
        return R.NOT_AUTHORIZED, "This profile can't use music apps."
    return R.NOT_AUTHORIZED, "This app can't do that."


__all__ = ["credentials_present", "password_matches", "resolve", "refusal_for"]
