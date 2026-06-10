"""Server-side enforcement of the launch PIN (#832).

Beckid: the admin "launch PIN" was a client-side overlay only — the
``launch_pin_required`` flag just told the frontend to draw a fixed-position
div over the app. Removing that div (Safari "Hide Distracting Items", devtools,
or any non-browser client like curl) gave full, unauthenticated access to every
``/api/*`` endpoint, because nothing on the server ever checked it.

``request_is_locked`` is the pure decision the ``before_request`` gate uses:
given the request path/method and the session's verified state, should this
request be blocked? Kept pure (no Flask) so the allow/deny matrix is unit-
testable without standing up the whole app.

Allow-list while locked (everything else → 401):
  * ``/`` and ``/static/`` and ``/favicon*`` — the page shell + lock-screen
    assets must load so the user can enter the PIN.
  * The unlock flow itself — current-profile probe, profile list/select for the
    picker, verify-launch-pin, reset-pin-via-credential, logout.
  * The public REST API ``/api/v1/`` — those routes carry their OWN
    ``@require_api_key`` auth and are built for headless automation, so a
    launch-locked UI shouldn't break a legitimate key holder. EXCEPT
    ``/api/v1/api-keys-internal*``, which are session-UI key management
    ("no auth required") and MUST stay locked — otherwise an attacker could
    mint a key and walk in through the public API.
"""

from __future__ import annotations

# GET endpoints the lock/picker screens need before a PIN is entered.
_ALLOWED_GET = frozenset({
    '/api/profiles',          # profile picker list (multi-profile launch)
    '/api/profiles/current',  # how the frontend detects the lock state
})

# POST endpoints that drive selection + unlock. Selecting a profile only sets
# session['profile_id'] (+ any per-profile PIN check); it does NOT set
# launch_pin_verified, so it can't bypass the launch lock.
_ALLOWED_POST = frozenset({
    '/api/profiles/select',
    '/api/profiles/verify-launch-pin',
    '/api/profiles/reset-pin-via-credential',
    '/api/profiles/logout',
})


def is_html_navigation(method: str, accept: str, sec_fetch_mode: str) -> bool:
    """True when a BLOCKED request is a top-level browser navigation (address
    bar, link, refresh) rather than a programmatic fetch/XHR.

    Such a request should be bounced to the root lock screen, not handed a raw
    JSON 401 — otherwise deep-linking/refreshing on a sub-page (e.g. /dashboard)
    while locked dumps JSON in the user's face (#832 follow-up). Programmatic
    fetches (Accept: */* or application/json) still get the JSON so the frontend
    can react to the lock.
    """
    if (method or 'GET').upper() != 'GET':
        return False
    if (sec_fetch_mode or '').strip().lower() == 'navigate':
        return True
    return 'text/html' in (accept or '').lower()


def request_is_locked(path: str, method: str, *,
                      require_pin: bool, pin_verified: bool) -> bool:
    """True when the launch-PIN gate must reject this request with 401."""
    if not require_pin or pin_verified:
        return False

    path = path or ''
    method = (method or 'GET').upper()

    # Page shell + assets needed to render the lock screen.
    if path == '/' or path.startswith('/static/') or path.startswith('/favicon'):
        return False

    # Key-authed public API — its own auth governs it. The session-UI key
    # management under it is the one exception that stays locked.
    if path.startswith('/api/v1/') and not path.startswith('/api/v1/api-keys-internal'):
        return False

    if method == 'GET' and path in _ALLOWED_GET:
        return False
    if method == 'POST' and path in _ALLOWED_POST:
        return False

    return True


__all__ = ['request_is_locked', 'is_html_navigation']
