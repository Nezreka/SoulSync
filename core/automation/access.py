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
- a system automation may be read and run by anyone (running one is "do this
  now", for the profile that asked), and changed only by an admin;
- the run_script action starts a program on the server, so only an admin may
  save an automation that uses it.

Pure decisions: the route layer passes in the row and the caller.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

DENIED = "This automation belongs to another profile or to the system. Ask an admin."
SCRIPT_DENIED = "Only an admin can save an automation that runs a script."


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
    return may_read(automation, profile_id, is_admin)


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


__all__ = ["DENIED", "SCRIPT_DENIED", "may_read", "may_run", "may_change", "uses_run_script"]
