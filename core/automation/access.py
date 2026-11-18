"""Who may read, change or run an automation.

Every profile has its own automations, and the system ones are the
install's. Until Nov 13 2026 the routes checked none of that: any profile
could open, edit, toggle, delete or run any automation by its id, another
profile's included (its notification settings hold webhook urls and bot
tokens), and could turn the install's own automations off. The master
pause switch was already admin only, for the same reason: it reaches past
your own automations.

- an admin may do anything;
- a profile may read, change and run its own automations;
- a system automation may be read by anyone, and run or changed only by an
  admin (S4: running one reached past your own things, "empty the recycle
  bin" for everyone);
- the run_script action starts a program on the server, so only an admin may
  save an automation that uses it;
- a member's own automations are personal (decision 4): their action is one
  of core.permissions.PERSONAL_AUTOMATION_ACTIONS (playlist pulls, their own
  watchlist scan, listening imports) and their steps only send notes. no
  run_script, no fire_signal (a signal wakes the install's automations), and
  owned_by is theirs to set only as "auto_sync" (the sync page's schedules).

Pure decisions: the route layer passes in the row and the caller.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

DENIED = "This automation belongs to another profile or to the system. Ask an admin."
SCRIPT_DENIED = "Only an admin can save an automation that runs a script."
NOT_PERSONAL = ("Your own automations can pull playlists, scan your watchlist and send you notes. "
                "That one is an admin's.")
OWNER_DENIED = "That automation can't be filed under that owner."

# the steps a member's automation may take after its action: notes, nothing else
NOTE_STEPS = frozenset({"discord_webhook", "pushbullet", "telegram", "ntfy", "gotify", "webhook"})
# owned_by: who a row is filed under. "system:*" rows are the engine's own
MEMBER_OWNERS = frozenset({"", "auto_sync"})
ADMIN_OWNERS = frozenset({"", "auto_sync", "video", "music"})


def _owner(automation: Mapping[str, Any]) -> Optional[int]:
    try:
        return int(automation.get("profile_id"))
    except (TypeError, ValueError):
        return None


def _same(profile_id: Any, owner: Optional[int]) -> bool:
    try:
        return owner is not None and int(profile_id) == owner
    except (TypeError, ValueError):
        return False


def may_read(automation: Optional[Mapping[str, Any]], profile_id: Any, is_admin: bool) -> bool:
    if is_admin or not automation:
        return True  # a missing row is the handler's 404, not a refusal
    return bool(automation.get("is_system")) or _same(profile_id, _owner(automation))


def may_run(automation: Optional[Mapping[str, Any]], profile_id: Any, is_admin: bool) -> bool:
    if is_admin or not automation:
        return True
    return not automation.get("is_system") and _same(profile_id, _owner(automation))


def may_change(automation: Optional[Mapping[str, Any]], profile_id: Any, is_admin: bool) -> bool:
    if is_admin or not automation:
        return True
    return not automation.get("is_system") and _same(profile_id, _owner(automation))


def uses_run_script(data: Optional[Mapping[str, Any]]) -> bool:
    """Does this create/update body ask for the run_script action anywhere?"""
    if not isinstance(data, Mapping):
        return False
    if str(data.get("action_type") or "").strip().lower() == "run_script":
        return True
    if str(data.get("notify_type") or "").strip().lower() == "run_script":
        return True
    then: Iterable[Any] = data.get("then_actions") or ()
    if not isinstance(then, (list, tuple)):
        return False
    return any(isinstance(step, Mapping)
               and str(step.get("type") or "").strip().lower() == "run_script"
               for step in then)


def _steps(data: Mapping[str, Any]) -> list:
    then = data.get("then_actions")
    if isinstance(then, str):
        import json
        try:
            then = json.loads(then or "[]")
        except ValueError:
            return [None]   # unreadable steps are not notes
    if then is None:
        then = []
    if not isinstance(then, (list, tuple)):
        return [None]   # unreadable steps are not notes
    out = [str(step.get("type") or "").strip().lower() if isinstance(step, Mapping) else None
           for step in then]
    notify = str(data.get("notify_type") or "").strip().lower()
    if notify and not out:
        out.append(notify)
    return out


def personal_refusal(data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """None when a member may keep this automation (its action is personal
    and its steps only send notes), else the reason. ``data`` is a create
    body or a stored row (then_actions as a list or its json)."""
    from core.permissions import PERSONAL_AUTOMATION_ACTIONS
    if not isinstance(data, Mapping):
        return NOT_PERSONAL
    action = str(data.get("action_type") or "").strip().lower()
    if action not in PERSONAL_AUTOMATION_ACTIONS:
        return NOT_PERSONAL
    if any(step not in NOTE_STEPS for step in _steps(data)):
        return NOT_PERSONAL
    return None


def owner_refusal(data: Optional[Mapping[str, Any]], is_admin: bool) -> Optional[str]:
    """None when owned_by (if the body sets it) is one this caller may file
    a row under, else the reason."""
    if not isinstance(data, Mapping) or "owned_by" not in data:
        return None
    raw = data.get("owned_by")
    if raw is not None and not isinstance(raw, str):
        return OWNER_DENIED
    value = (raw or "").strip()
    return None if value in (ADMIN_OWNERS if is_admin else MEMBER_OWNERS) else OWNER_DENIED


def merged(stored: Optional[Mapping[str, Any]], body: Optional[Mapping[str, Any]]) -> dict:
    """what a row would be after an update body lands on it (the fields the
    personal check reads)."""
    out = dict(stored or {})
    for key in ("action_type", "then_actions", "notify_type", "owned_by"):
        if isinstance(body, Mapping) and key in body:
            out[key] = body[key]
    if isinstance(body, Mapping) and "then_actions" in body and "notify_type" not in body:
        out["notify_type"] = None
    return out


__all__ = ["DENIED", "SCRIPT_DENIED", "NOT_PERSONAL", "OWNER_DENIED", "NOTE_STEPS", "may_read", "may_run",
           "may_change", "uses_run_script", "personal_refusal", "owner_refusal", "merged"]
