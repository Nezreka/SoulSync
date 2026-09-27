"""what happens around an issue besides the row itself: timeline events for
triage, who hears about a change, and the one-click fix each category points
at. shared by music and video; each api passes its own store and notifier.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

_STATUS_WORDS = {
    "open": "reopened this",
    "in_progress": "is working on this",
    "resolved": "marked this fixed",
    "dismissed": "closed this without a change",
}

# category -> the tool that fixes it. the frontend owns HOW (it knows the
# modals); the id here is the contract. None = nothing to click, talk it out.
FIX_ACTIONS = {
    # music
    "wrong_track": ("reidentify", "Re-identify track"),
    "wrong_album": ("reidentify", "Re-identify"),
    "wrong_artist": ("reidentify", "Re-identify"),
    "wrong_metadata": ("edit_metadata", "Edit details"),
    "wrong_cover": ("pick_art", "Pick new cover"),
    "audio_quality": ("redownload", "Find a better copy"),
    "missing_tracks": ("wishlist_missing", "Wishlist missing tracks"),
    "incomplete_album": ("wishlist_missing", "Wishlist missing tracks"),
    "duplicate_tracks": ("find_duplicates", "Find duplicates"),
    # video
    "wrong_match": ("fix_match", "Fix the match"),
    "wrong_poster": ("pick_art", "Pick new poster"),
    "bad_quality": ("upgrade", "Search for an upgrade"),
    "missing_content": ("wishlist_missing", "Wishlist missing episodes"),
    "duplicate": ("find_duplicates", "Review copies"),
}


def fix_action(category: str) -> Optional[Dict[str, str]]:
    hit = FIX_ACTIONS.get(category or "")
    return {"id": hit[0], "label": hit[1]} if hit else None


def timeline_events(before: Dict[str, Any], updates: Dict[str, Any]) -> List[str]:
    """plain sentences for the thread, one per triage change that happened."""
    out = []
    status = updates.get("status")
    if status and status != before.get("status"):
        out.append(_STATUS_WORDS.get(status, f"set the status to {status}"))
    prio = updates.get("priority")
    if prio and prio != before.get("priority"):
        out.append(f"set the priority to {prio}")
    cat = updates.get("category")
    if cat and cat != before.get("category"):
        out.append("changed the category")
    return out


def recipients(issue: Dict[str, Any], follower_ids: Iterable[int], actor_id) -> List[int]:
    """the reporter and everyone following, minus whoever just acted."""
    ids = []
    for pid in [issue.get("profile_id"), *follower_ids]:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid not in ids and pid != actor_id:
            ids.append(pid)
    try:
        actor = int(actor_id)
    except (TypeError, ValueError):
        actor = None
    return [p for p in ids if p != actor]


def tell(notify: Callable[[int, str, str], Any], people: Iterable[int], message: str,
         kind: str = "info") -> None:
    for pid in people:
        try:
            notify(pid, message, kind)
        except Exception:  # noqa: BLE001, S110 - a note never fails the triage
            pass


__all__ = ["FIX_ACTIONS", "fix_action", "timeline_events", "recipients", "tell"]
