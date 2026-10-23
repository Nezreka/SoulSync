"""WebSocket access gate (#852).

Flask's ``before_request`` — where the launch-PIN / login gate lives — does NOT
run for the socketio handshake, so an unauthenticated client that removes the
login/PIN overlay (Safari "Hide Distracting Items", devtools, curl) can still open
a socket and receive the live data SoulSync streams over it (downloads, logs,
dashboard, notifications). The connect handler must therefore enforce the same
check the HTTP gate does.

Pure decisions so they're unit-testable; the socketio handler injects the
live session/config/header values plus the session's revocation state.
Mirrors the HTTP gate precedence exactly: login mode (when on) replaces the
launch PIN.

Revocation (#1837): the HTTP gate also drops a session whose profile epoch has
moved on ("sign out everywhere") or whose device row was revoked (logout, or
an admin signing that browser out). Those checks read server-side state, not
the cookie, so a *replayed* cookie fails them on HTTP — but the flags inside
the cookie itself stay set, so the WS gate must apply the same revocation
checks or the replayed cookie still opens a socket.
"""

from __future__ import annotations


def is_session_revoked(
    *,
    has_profile_session: bool,
    epoch_current: bool,
    device_live: bool,
) -> bool:
    """True ⇒ this session's server-side revocation state kills it.

    Mirrors the HTTP check, which only runs when the session carries a
    profile: no profile in the session ⇒ nothing to revoke. The live values
    are injected by the caller using the same helpers HTTP uses
    (``session_is_current`` / ``device_is_live``), which fail OPEN on a db
    hiccup — a read error must not sign everyone out.
    """
    if not has_profile_session:
        return False
    return not (epoch_current and device_live)


def is_ws_connection_blocked(
    *,
    require_login: bool,
    login_authenticated: bool,
    require_pin: bool,
    pin_verified: bool,
    proxy_authed: bool,
    session_revoked: bool = False,
) -> bool:
    """True ⇒ reject this WebSocket connection.

    - Revoked session → always blocked (the cookie's flags no longer matter).
    - Login mode on  → must be login-authenticated.
    - Else PIN on    → must have verified the PIN (or be trusted by an auth proxy).
    - Neither on     → open (matches the HTTP gate's no-op default).
    """
    if session_revoked:
        return True
    if require_login:
        return not login_authenticated
    if require_pin:
        return not (pin_verified or proxy_authed)
    return False


__all__ = ["is_ws_connection_blocked", "is_session_revoked"]
