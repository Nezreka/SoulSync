"""Which profile a web request acts as.

the old rule was ``session.get('profile_id', 1)``: no profile in the session
meant the admin. that turned three ordinary things into admin rights:

  * a member posting /api/profiles/logout (it popped profile_id, kept the
    login/launch-pin flags, and the next request was profile 1)
  * a member whose profile got deleted (the gate popped the stale id, and
    the next request was profile 1)
  * a fresh browser on a multi-profile install, before anyone picked a card

the rule now: a session profile wins. with none, a single-profile install is
still the admin (one person, trust the lan, nothing changes for them). with
more than one profile, or in login mode, no profile means no rights until one
is picked.

pure so the matrix is unit-testable without the app.
"""

from __future__ import annotations

from typing import Optional


def resolve_session_profile(*, session_pid, login_mode: bool,
                            profile_count: int) -> Optional[int]:
    """the profile id this request acts as, or None for no rights."""
    try:
        pid = int(session_pid) if session_pid is not None else None
    except (TypeError, ValueError):
        pid = None
    if pid is not None and pid > 0:
        return pid
    if login_mode:
        return None
    if profile_count <= 1:
        return 1
    return None


# oauth callbacks carry their profile in the state param and can land on a
# host with no session cookie, so they stay open. so does /status (uptime
# monitors poll it).
_DATA_PREFIXES = ('/api/', '/stream/', '/auth/')

# what a browser with no profile picked yet may still reach: the page shell,
# the picker and the unlock flows. the same shape as the launch lock's list.
_ALLOWED_GET = frozenset({
    '/api/profiles',
    '/api/profiles/current',
    '/api/setup/status',
    '/api/auth/recovery-question',
})

_ALLOWED_POST = frozenset({
    '/api/profiles/select',
    '/api/profiles/verify-launch-pin',
    '/api/profiles/reset-pin-via-credential',
    '/api/profiles/logout',
    '/api/auth/login',
    '/api/auth/logout',
    '/api/auth/recovery-reset',
})


def is_open_profile_path(path: str, method: str) -> bool:
    """paths every gate leaves open: accepting an invite link (the token is
    the credential, an admin minted it) and a profile's avatar image, which
    the picker draws before anyone is signed in."""
    import re
    method = (method or 'GET').upper()
    if path.startswith('/api/invite/') and method in ('GET', 'POST'):
        return True
    return method == 'GET' and re.fullmatch(r'/api/profiles/\d+/avatar', path or '') is not None


def no_profile_request_is_blocked(path: str, method: str) -> bool:
    """True when a request with no profile must be turned away."""
    path = path or ''
    method = (method or 'GET').upper()
    # page shells (deep links like /library) render the picker themselves;
    # only these carry data or act for a profile
    if not path.startswith(_DATA_PREFIXES):
        return False
    # key-authed public api: the key is the credential there
    if path.startswith('/api/v1/') and not path.startswith('/api/v1/api-keys-internal'):
        return False
    if method == 'GET' and path in _ALLOWED_GET:
        return False
    if is_open_profile_path(path, method):
        return False
    if method == 'POST' and path in _ALLOWED_POST:
        return False
    return True


__all__ = ['resolve_session_profile', 'no_profile_request_is_blocked', 'is_open_profile_path']
