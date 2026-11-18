"""whose event this is (#2166 follow-up 25).

each profile only cares about its own events; admins hear everything. an
event that concerns one person's things (their download batch, their
request being approved, their playlist syncing) carries that person as
``_event_owner``; a member's own automation runs only on events it owns.

the owner is one profile id, or a list when one event covers several people
(an approval that answers everyone who asked, a release several profiles
wanted). install-wide events (scans, database updates, maintenance,
backups) never carry an owner, so they reach admins only, even when a
member's request or job happened to set them off.

the automation engine stamps the request's or background job's profile on
anything that names no owner. that's right for "I did this" events, and
wrong whenever the one acting isn't the one it's about (an admin approving
someone's request), so those sites name the owner here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

OWNER_KEY = "_event_owner"

# events about the install, not anyone's things: never stamped, never heard
# by a member's automation
INSTALL_EVENTS = frozenset({
    "database_update_completed", "library_scan_completed", "duplicate_scan_completed",
    "quality_scan_completed", "music_repair_scan_completed", "music_repair_finding_created",
    "deezer_signin_failed", "deezer_signin_restored", "webhook_received",
    "audiobook_library_scan_completed",
    "video_library_scan_completed", "video_database_update_completed", "video_collections_synced",
    "video_overlays_applied", "video_repair_finding_created", "video_repair_scan_completed",
    # the video queue draining: every profile's downloads at once
    "video_batch_complete",
})


def _ids(owner: Any) -> List[int]:
    if owner is None or isinstance(owner, bool):
        return []
    if isinstance(owner, (list, tuple, set, frozenset)):
        out: List[int] = []
        for o in owner:
            for i in _ids(o):
                if i not in out:
                    out.append(i)
        return out
    try:
        i = int(str(owner).strip())
    except (TypeError, ValueError):
        return []
    return [i] if i > 0 else []


def owner_value(owner: Any) -> Optional[Union[int, List[int]]]:
    """the stamp for ``owner`` (an id, ids, or None): one int, a sorted list
    for several, None for nobody."""
    ids = _ids(owner)
    if not ids:
        return None
    return ids[0] if len(ids) == 1 else sorted(ids)


def owned(data: Optional[Dict[str, Any]], owner: Any) -> Dict[str, Any]:
    """``data`` with its owner named. nobody known leaves it unnamed (the
    engine then stamps whoever is acting, or no one in the background)."""
    out = dict(data or {})
    value = owner_value(owner)
    if value is not None:
        out[OWNER_KEY] = value
    return out


def owners_of(data: Optional[Dict[str, Any]]) -> List[int]:
    """the profile ids an event belongs to (empty: the install's)."""
    return _ids((data or {}).get(OWNER_KEY))


__all__ = ["OWNER_KEY", "INSTALL_EVENTS", "owner_value", "owned", "owners_of"]
