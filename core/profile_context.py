"""Background profile context.

Work that runs OUTSIDE a web request — the automation engine, scheduled jobs —
has no Flask session, so ``get_current_profile_id()`` falls back to admin
(profile 1). That's wrong for an automation owned by a non-admin: their
playlist pull, their per-profile writes, should act as THEM.

This lets the engine declare "the work below is running for profile X" around a
unit of background work (set/reset in a try/finally). ``get_current_profile_id``
consults it only when there's no real request — so an actual logged-in session
always wins, and nothing changes for foreground/admin paths. Built on a
``ContextVar`` so the value is scoped to the running call and reset cleanly,
even on thread-pool reuse.
"""

from __future__ import annotations

import contextvars

_background_profile_id: "contextvars.ContextVar[int | None]" = contextvars.ContextVar(
    "background_profile_id", default=None
)


def set_background_profile(profile_id):
    """Declare the profile for the current background unit of work. Returns a
    token to pass to reset_background_profile (use in try/finally)."""
    return _background_profile_id.set(profile_id)


def reset_background_profile(token) -> None:
    """Restore the previous background profile (clears the override)."""
    try:
        _background_profile_id.reset(token)
    except Exception:
        # Token from a different context — clear to the default rather than leak.
        _background_profile_id.set(None)


def get_background_profile():
    """The background profile in effect, or None if none is set."""
    return _background_profile_id.get()


__all__ = ["set_background_profile", "reset_background_profile", "get_background_profile",
           "get_current_profile_id", "is_admin_request", "admin_only"]


# ── request-side accessors ───────────────────────────────────────────────────
# These lived in web_server.py; they moved here in the decomposition because
# api/* blueprint modules need them at IMPORT time (admin_only is applied as a
# decorator), and importing web_server from api/* is circular. web_server
# imports them back from here — one definition, same objects everywhere.

def get_current_profile_id() -> int:
    """The current profile id: the request's, else the background override, else 1.

    Background callers (automation engine, sync threads, watchlist scanner) have
    no request context, so ``g.profile_id`` raises ``RuntimeError`` rather than
    ``AttributeError`` — catch both so non-request callers degrade to the admin
    profile instead of crashing the handler. A real web request always wins;
    only with NO request does the background-profile override apply.
    """
    from flask import g
    try:
        return g.profile_id
    except (AttributeError, RuntimeError):
        pass
    pid = get_background_profile()
    return pid if pid is not None else 1


def is_admin_request() -> bool:
    """Is the current request from an admin profile?

    profile 1 always is. any other profile is an admin when its is_admin
    flag says so: the before_request hook stashes that on g.is_admin, and
    when the hook skipped this path (/api/profiles, /api/v1, static) the
    flag is read from the profile row once and cached on g. the shell shows
    Settings and the other admin surfaces to every is_admin profile, the
    video gate and the newer music endpoints check the same flag; this
    gate used to be profile 1 only, so a second admin got the page and a
    403 behind it.
    """
    from flask import g

    pid = get_current_profile_id()
    if pid == 1:
        return True
    try:
        cached = getattr(g, 'is_admin', None)
    except RuntimeError:
        cached = None
    if cached is not None:
        return bool(cached)
    is_admin = False
    try:
        from database.music_database import get_database
        profile = get_database().get_profile(pid)
        is_admin = bool((profile or {}).get('is_admin', False))
    except Exception:  # noqa: BLE001 - an unreadable profile is not an admin
        is_admin = False
    try:
        g.is_admin = is_admin
    except RuntimeError:
        pass
    return is_admin


def admin_only(view_fn):
    """Restrict a Flask view to admin profiles (see is_admin_request).

    Settings-class endpoints expose / mutate service tokens, OAuth secrets and
    API keys; non-admin profiles must not see them. NOTE on the auth model:
    ``get_current_profile_id()`` defaults to 1 with no session, so single-admin
    installs have no actual gate here — this gates non-admin profiles in
    MULTI-profile setups, not the network. That posture is the project's
    existing model.
    """
    import functools

    from flask import jsonify

    @functools.wraps(view_fn)
    def wrapper(*args, **kwargs):
        if not is_admin_request():
            return jsonify({
                "success": False,
                "error": "Admin access required",
            }), 403
        return view_fn(*args, **kwargs)
    return wrapper
