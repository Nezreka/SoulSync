"""which profiles a paired device may pick (PAIRING_SPEC.md 4).

a music app only sees profiles with "Use music apps" (music.apps): on for
admins and members by default, off on the Kids template. a turned-off
profile is never offered. the same rule is held on every request the
device makes after its pick (``may_use``), so turning the permission off
stops a device that already picked.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from core.connect import device_tokens as T

logger = logging.getLogger(__name__)

# the permission each kind of app needs
KIND_PERMISSION = {T.KIND_MUSIC: "music.apps"}


def may_use(db, profile: Optional[Mapping[str, Any]], kind: str) -> bool:
    """may a device of this kind act as this profile? fails closed."""
    if not profile:
        return False
    if profile.get("disabled") and not profile.get("is_admin"):
        return False
    perm = KIND_PERMISSION.get(kind)
    if perm is None:
        return False
    from core import permission_store
    from core.permissions import can
    try:
        ctx = permission_store.context_for(db, profile.get("id"))
    except Exception:  # noqa: BLE001
        logger.warning("device pick: no permissions for profile %s", profile.get("id"), exc_info=True)
        return False
    return can(ctx, perm)


def pick_list(db, kind: str, *, login_mode: bool) -> List[Dict[str, Any]]:
    """the profiles a device of this kind may pick, with what each asks
    for at the pick: "password" in login mode, else "pin" when it has one."""
    out = []
    for p in db.get_all_profiles():
        full = db.get_profile(p["id"]) or {}
        if not may_use(db, full, kind):
            continue
        if login_mode:
            needs = "password"
        else:
            needs = "pin" if full.get("has_pin") else None
        out.append({"id": full.get("id"), "name": full.get("name"),
                    "avatar_color": full.get("avatar_color"), "avatar_url": full.get("avatar_url"),
                    "needs": needs})
    return out


__all__ = ["KIND_PERMISSION", "may_use", "pick_list"]
