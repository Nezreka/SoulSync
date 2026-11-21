"""which routes a paired device may call (PAIRING_SPEC.md 4).

nothing is allowed by default. a route opts in with one of two marks:

* ``@device_pick``: an unprofiled device may call it (the pick list and
  the pick itself). a device that already picked may call it too; the
  route says what that means.
* ``@device_playback``: a device that picked a profile may call it, as
  that profile, with playback scope: browse, stream, lyrics, artwork,
  scrobbles and play history, favorites, playlists, the queue. never
  settings, downloads admin, users, API keys or anything admin only.

the marks are read through functools.wraps layers like the route
inventory's (core.permissions.declared_access), so they work under or
over @admin_only, @public and the rest. tests/test_device_token_battery.py
walks every rule of the real app and holds each to this.

the gate (``decide``) is pure; web_server's before_request hook feeds it.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from core.connect import device_tokens as T

DEVICE_ATTR = "_soulsync_device"
PICK = "pick"
PLAYBACK = "playback"
TOKEN_HEADER = "X-Device-Token"


def _mark(fn, value):
    setattr(fn, DEVICE_ATTR, value)
    return fn


def device_pick(view_fn):
    """an unprofiled device may call this route (see the module doc)."""
    return _mark(view_fn, PICK)


def device_playback(view_fn):
    """a device that picked its profile may call this route as that
    profile, playback scope (see the module doc)."""
    return _mark(view_fn, PLAYBACK)


def device_access(view_fn) -> Optional[str]:
    """PICK | PLAYBACK | None, looking through functools.wraps layers."""
    fn, seen = view_fn, 0
    while fn is not None and seen < 20:
        mark = getattr(fn, DEVICE_ATTR, None)
        if mark:
            return mark
        fn = getattr(fn, "__wrapped__", None)
        seen += 1
    return None


def request_token(headers) -> str:
    """the device token a request carries, or "". ``X-Device-Token``, or
    ``Authorization: Bearer sdt_...`` (an sk_ key in that header is not a
    device token and is left to the api key check). never a query param:
    a token in a url ends up in logs."""
    value = (headers.get(TOKEN_HEADER) or "").strip()
    if value:
        return value
    scheme, _, rest = (headers.get("Authorization") or "").partition(" ")
    rest = rest.strip()
    if scheme.lower() == "bearer" and rest.startswith(T.TOKEN_PREFIX):
        return rest
    return ""


# what decide() answers: (status, error code, words) for a refusal, or
# ("ok", None, None)
OK = "ok"


def decide(row: Optional[dict], access: Optional[str], now: float) -> Tuple[Any, Optional[str], Optional[str]]:
    """may this device make this request? ``row`` is its device_tokens
    row (None: unknown token), ``access`` the route's mark."""
    state = T.state_of(row, now)
    if state is None or state == T.REVOKED:
        return 401, "device_token_invalid", "This device isn't paired with this server any more."
    if state == T.EXPIRED:
        return 401, "device_pick_expired", "No profile was picked in time. Pair this device again."
    if access is None:
        return 403, "device_not_allowed", "Paired devices can't use this."
    if state == T.UNPROFILED and access != PICK:
        return 403, "device_pick_first", "Pick a profile first."
    if state == T.LIVE and (row or {}).get("scope") != T.SCOPE_PLAYBACK:
        # a scope this code doesn't know gets nothing
        return 403, "device_not_allowed", "Paired devices can't use this."
    return OK, None, None


__all__ = ["DEVICE_ATTR", "PICK", "PLAYBACK", "TOKEN_HEADER", "device_pick", "device_playback",
           "device_access", "request_token", "decide", "OK"]
