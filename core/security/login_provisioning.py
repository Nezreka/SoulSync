"""Login-mode password provisioning policy.

Invariant: while ``security.require_login`` is on, every profile must have a login
password — otherwise it's fail-closed locked out (usable only after the admin
provisions one). That's not a security hole (no-password = can't get in), but it's
a usability gap, and the point here is to make it impossible to OPEN one from any
write-point: creating a profile, clearing a password, or flipping login mode on.

These are pure decisions so they're the single source of truth + unit-testable;
web_server wires them into the create / set-password / enable-login endpoints, and
the UI mirrors them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def members_without_password(profiles: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Non-admin profiles with no login password — they can't sign in once login
    mode is on. The admin is covered separately by its own anti-lockout, so it's
    excluded here. Returns ``[{'id', 'name'}, …]`` (empty = no gap)."""
    out: List[Dict[str, Any]] = []
    for p in (profiles or []):
        if not p.get('is_admin') and not p.get('has_password'):
            out.append({'id': p.get('id'), 'name': p.get('name')})
    return out


def create_needs_password(require_login: bool, is_admin: bool = False) -> bool:
    """A non-admin profile created while login mode is on must carry a password,
    or it's born unable to sign in."""
    return bool(require_login) and not is_admin


def removing_password_strands(require_login: bool) -> bool:
    """Clearing a profile's password while login mode is on would lock it out."""
    return bool(require_login)


__all__ = [
    "members_without_password",
    "create_needs_password",
    "removing_password_strands",
]
