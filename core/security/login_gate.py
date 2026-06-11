"""Pure gate decision for opt-in username/password login mode.

When ``security.require_login`` is on, every request must come from an
authenticated session; unauthenticated requests are blocked except the page shell,
the login/logout flow, and the key-authed public API. This is the per-user
equivalent of (and replacement for) the shared launch-PIN gate.

Deliberately does NOT allowlist the profile LIST or picker — in login mode you log
in by typing your name + password, you don't pick from an exposed roster.
"""

from __future__ import annotations

# GET endpoints the login screen itself needs before auth.
_ALLOWED_GET = frozenset({
    '/api/profiles/current',  # how the frontend detects login state
    '/api/setup/status',      # first-run check runs before the login screen
})

# POST endpoints that drive the login flow.
_ALLOWED_POST = frozenset({
    '/api/auth/login',
    '/api/auth/logout',
})


def login_request_is_blocked(path: str, method: str, *,
                             require_login: bool, authenticated: bool) -> bool:
    """True when the login gate must reject this request (login mode on + the
    session isn't authenticated and the path isn't part of the login flow)."""
    if not require_login or authenticated:
        return False

    path = path or ''
    method = (method or 'GET').upper()

    # Page shell + assets needed to render the login screen.
    if path == '/' or path.startswith('/static/') or path.startswith('/favicon'):
        return False

    # Key-authed public API governs itself (its own key auth).
    if path.startswith('/api/v1/') and not path.startswith('/api/v1/api-keys-internal'):
        return False

    if method == 'GET' and path in _ALLOWED_GET:
        return False
    if method == 'POST' and path in _ALLOWED_POST:
        return False

    return True


__all__ = ['login_request_is_blocked']
