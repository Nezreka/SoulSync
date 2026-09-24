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
from typing import Optional

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
           "get_current_profile_id", "is_admin_profile", "is_admin_request",
           "admin_only"]


# ── request-side accessors ───────────────────────────────────────────────────
# These lived in web_server.py; they moved here in the decomposition because
# api/* blueprint modules need them at IMPORT time (admin_only is applied as a
# decorator), and importing web_server from api/* is circular. web_server
# imports them back from here — one definition, same objects everywhere.

def get_current_profile_id() -> Optional[int]:
    """The current profile id: the request's, else the background override, else 1.

    Background callers (automation engine, sync threads, watchlist scanner) have
    no request context, so ``g.profile_id`` raises ``RuntimeError`` rather than
    ``AttributeError`` — catch both so non-request callers degrade to the admin
    profile instead of crashing the handler. A real web request always wins;
    only with NO request does the background-profile override apply.
    """
    from flask import g, has_request_context
    if has_request_context():
        try:
            if hasattr(g, 'profile_id'):
                return g.profile_id
        except RuntimeError:
            pass
        from flask import session
        try:
            from core.settings import config_manager
            req_login = bool(config_manager.get('security.require_login', False)) if config_manager else False
            if req_login and not session.get('login_authenticated', False):
                return None

            req_pin = bool(config_manager.get('security.require_pin_on_launch', False)) if config_manager else False
            if req_pin and not req_login:
                from flask import request
                from core.security.auth_proxy import trusted_proxy_user
                proxy_hdr = (config_manager.get('security.auth_proxy_header', '') or '') if config_manager else ''
                proxy_authed = bool(trusted_proxy_user(request.headers.get, proxy_hdr))
                if not session.get('launch_pin_verified', False) and not proxy_authed:
                    return None
        except Exception:  # noqa: S110 — unreadable config falls through to session/background
            pass
        try:
            sess_pid = session.get('profile_id')
            if sess_pid is not None:
                return sess_pid
        except Exception:  # noqa: S110 — unreadable session falls through to background override
            pass
        pid = get_background_profile()
        return pid if pid is not None else 1

    pid = get_background_profile()
    return pid if pid is not None else 1


def is_admin_profile(profile_id, *, database=None) -> bool:
    """Is THIS profile id an admin? The request-free half of the gate.

    ``is_admin_request`` answers for the caller of the current request; this
    answers for a profile id that was handed to a function as an argument.
    Library v2 and the acquisition layer need the second form: their admin
    checks take an ``actor_profile_id`` parameter and run on background
    threads as well as in a request, so they cannot reach for ``g``. Both
    forms have to agree, or a second admin passes the route gate and is
    refused one layer down — which is exactly what a literal ``== 1`` did
    to every one of them.

    Profile 1 is always an admin; anything unreadable is not.

    ``database`` lets a caller that already holds one hand it in. That is not
    a convenience: api/library_v2 is wired with an injected ``get_database``
    precisely so its routes never touch the app-wide singleton, and reaching
    for the singleton from inside it opened (and schema-initialised) a second,
    unrelated database — in the test harness the real one, which then blocked
    on the write lock the caller was already holding.
    """
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return False
    if pid == 1:
        return True
    try:
        db = database
        if db is None:
            from database.music_database import get_database
            db = get_database()
        return bool((db.get_profile(pid) or {}).get('is_admin', False))
    except Exception:  # noqa: BLE001 - an unreadable profile is not an admin
        return False


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
    from flask import g, has_request_context, session

    if has_request_context():
        try:
            from core.settings import config_manager
            req_login = bool(config_manager.get('security.require_login', False)) if config_manager else False
            if req_login and not session.get('login_authenticated', False):
                return False

            req_pin = bool(config_manager.get('security.require_pin_on_launch', False)) if config_manager else False
            if req_pin:
                from flask import request
                from core.security.auth_proxy import trusted_proxy_user
                proxy_hdr = (config_manager.get('security.auth_proxy_header', '') or '') if config_manager else ''
                proxy_authed = bool(trusted_proxy_user(request.headers.get, proxy_hdr))
                if not session.get('launch_pin_verified', False) and not proxy_authed:
                    return False
        except Exception:  # noqa: S110 — unreadable config falls through to profile inspection
            pass

        try:
            cached = getattr(g, 'is_admin', None)
            if cached is not None:
                return bool(cached)
        except RuntimeError:
            pass

    pid = get_current_profile_id()
    if pid is None:
        return False
    if pid == 1:
        return True
    is_admin = is_admin_profile(pid)
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
