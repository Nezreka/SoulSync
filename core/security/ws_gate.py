"""WebSocket access gate (#852).

Flask's ``before_request`` — where the launch-PIN / login gate lives — does NOT
run for the socketio handshake, so an unauthenticated client that removes the
login/PIN overlay (Safari "Hide Distracting Items", devtools, curl) can still open
a socket and receive the live data SoulSync streams over it (downloads, logs,
dashboard, notifications). The connect handler must therefore enforce the same
check the HTTP gate does.

Pure decision so it's unit-testable; the socketio handler injects the live
session/config/header values. Mirrors the HTTP gate precedence exactly: login
mode (when on) replaces the launch PIN.
"""

from __future__ import annotations


def is_ws_connection_blocked(
    *,
    require_login: bool,
    login_authenticated: bool,
    require_pin: bool,
    pin_verified: bool,
    proxy_authed: bool,
) -> bool:
    """True ⇒ reject this WebSocket connection.

    - Login mode on  → must be login-authenticated.
    - Else PIN on    → must have verified the PIN (or be trusted by an auth proxy).
    - Neither on     → open (matches the HTTP gate's no-op default).
    """
    if require_login:
        return not login_authenticated
    if require_pin:
        return not (pin_verified or proxy_authed)
    return False


__all__ = ["is_ws_connection_blocked"]
