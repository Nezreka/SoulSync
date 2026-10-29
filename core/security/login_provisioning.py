"""Login-mode provisioning policy.

Invariant: while ``security.require_login`` is on, every profile must have a
way to sign in, or it's fail-closed locked out (usable only after the admin
fixes it). That's not a security hole (no way in = can't get in), but it's a
usability gap, and the point here is to make it impossible to OPEN one from
any write-point: creating a profile, clearing a password, disconnecting an
account, turning a sign-in method off, or flipping login mode on.

A way in is a password, or a Plex or OIDC link whose sign-in is turned on.
A link to a sign-in that's switched off doesn't count. While OIDC-only is on
(#1839), the password and Plex don't count either.

Profile 1 is the admin and keeps its own anti-lockout: login mode can't be
turned on without its password, and that password can't be cleared while
login mode is on. It's the way back in when the provider is unreachable and
the OIDC-only override is set.

These are pure decisions so they're the single source of truth + unit-testable;
web_server and the profile api wire them into the endpoints, and the UI
mirrors them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

ADMIN_ID = 1


@dataclass(frozen=True)
class SignInWays:
    """which sign-ins are open right now"""
    password: bool = True
    plex: bool = False
    oidc: bool = False

    @classmethod
    def from_config(cls, config_manager, *, oidc_only: bool = False) -> "SignInWays":
        """read from the settings (or anything with .get). oidc_only is the
        caller's: the override decides it, not the stored value alone"""
        from core.security import oidc_signin

        def _b(key):
            try:
                return bool(config_manager.get(key, False))
            except Exception:  # noqa: BLE001
                return False

        oidc = oidc_signin.read_settings(config_manager) is not None
        return cls(password=not oidc_only, plex=_b("security.plex_signin") and not oidc_only, oidc=oidc)


def has_a_way_in(methods: Optional[Dict[str, bool]], ways: SignInWays) -> bool:
    """methods = profile_sign_in_methods(pid): {'password', 'plex', 'oidc'}"""
    m = methods or {}
    return bool((m.get("password") and ways.password)
                or (m.get("plex") and ways.plex)
                or (m.get("oidc") and ways.oidc))


def members_without_a_way_in(profiles: Optional[List[Dict[str, Any]]],
                             methods_of: Callable[[int], Dict[str, bool]],
                             ways: SignInWays) -> List[Dict[str, Any]]:
    """profiles that couldn't sign in with these ways open. Profile 1 is
    left to its own anti-lockout. Returns ``[{'id', 'name'}, ...]``"""
    out: List[Dict[str, Any]] = []
    for p in (profiles or []):
        pid = p.get("id")
        if pid is None or int(pid) == ADMIN_ID:
            continue
        try:
            methods = methods_of(int(pid))
        except Exception:  # noqa: BLE001 - unreadable reads as no way in
            methods = {}
        if not has_a_way_in(methods, ways):
            out.append({"id": pid, "name": p.get("name")})
    return out


def profiles_without_oidc(profiles: Optional[List[Dict[str, Any]]],
                          methods_of: Callable[[int], Dict[str, bool]]) -> List[Dict[str, Any]]:
    """profiles that could be used but have no OIDC link: they'd be locked
    out by OIDC-only. Profile 1 counts; a turned-off profile doesn't"""
    out: List[Dict[str, Any]] = []
    for p in (profiles or []):
        pid = p.get("id")
        if pid is None:
            continue
        if p.get("disabled") and int(pid) != ADMIN_ID:
            continue
        try:
            linked = bool((methods_of(int(pid)) or {}).get("oidc"))
        except Exception:  # noqa: BLE001
            linked = False
        if not linked:
            out.append({"id": pid, "name": p.get("name")})
    return out


def create_refusal(require_login: bool, *, has_password: bool, oidc_only: bool = False,
                   is_admin: bool = False) -> Optional[str]:
    """why a profile made by hand can't be created, or None. A profile made
    by hand has no links, so in login mode its only way in is a password,
    and under OIDC-only it has none at all: an invite links it instead"""
    if oidc_only:
        return ("Sign-in is OIDC only: send an invite instead, or turn on OIDC sign-up "
                "so new people get a profile when they first sign in.")
    if require_login and not is_admin and not has_password:
        return "Login mode is on — give this profile a login password so they can sign in."
    return None


def removing_password_strands(require_login: bool, *, profile_id: Optional[int] = None,
                              methods: Optional[Dict[str, bool]] = None,
                              ways: Optional[SignInWays] = None) -> bool:
    """Clearing a profile's password while login mode is on would lock it
    out, unless it has another way in. The admin's never comes off."""
    if not require_login:
        return False
    if profile_id is not None and int(profile_id) == ADMIN_ID:
        return True
    if methods is None or ways is None:
        return True
    rest = dict(methods)
    rest["password"] = False
    return not has_a_way_in(rest, ways)


def removing_oidc_strands(require_login: bool, *, methods: Optional[Dict[str, bool]],
                          ways: SignInWays) -> bool:
    """Disconnecting OIDC while login mode is on would lock the profile out
    unless it has another way in. Under OIDC-only it never has one."""
    if not require_login:
        return False
    rest = dict(methods or {})
    rest["oidc"] = False
    return not has_a_way_in(rest, ways)


__all__ = [
    "SignInWays",
    "has_a_way_in",
    "members_without_a_way_in",
    "profiles_without_oidc",
    "create_refusal",
    "removing_password_strands",
    "removing_oidc_strands",
]
