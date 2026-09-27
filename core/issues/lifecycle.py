"""issue lifecycle rules, one copy for music and video.

before this lived twice and drifted: music took any status/priority string an
admin sent (a stray "closed" fell out of every filter), video only capped
lengths at create, video reopen-to-in_progress kept stale resolved stamps,
and a reporter could file their own issue as high priority to jump the queue.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

STATUSES = ("open", "in_progress", "resolved", "dismissed")
CLOSED_STATUSES = ("resolved", "dismissed")
PRIORITIES = ("low", "normal", "high")

TITLE_MAX = 200
DESCRIPTION_MAX = 2000
RESPONSE_MAX = 2000

# what an owner may change on their own issue; everything else is triage
OWNER_FIELDS = ("title", "description")
ADMIN_FIELDS = ("title", "description", "status", "priority", "category", "admin_response")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def clean_new_issue(body: Dict[str, Any], *, is_admin: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(fields, error) for a new report: trimmed, capped, priority set.
    a reporter can't pick priority, that's the admin's call."""
    title = str(body.get("title") or "").strip()[:TITLE_MAX]
    if not title:
        return None, "title is required"
    description = str(body.get("description") or "").strip()[:DESCRIPTION_MAX]
    priority = body.get("priority") if is_admin else "normal"
    if priority not in PRIORITIES:
        priority = "normal"
    return {"title": title, "description": description, "priority": priority}, None


def clean_update(body: Dict[str, Any], *, is_admin: bool, is_owner: bool,
                 actor_id: int, categories=None) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """(updates, error, http_status) for a PUT.

    owners may edit title/description only, any other key is refused outright
    (never a silent partial apply). admins may triage; every value is checked
    against the enums, text is capped, and a status change stamps or clears
    resolved_by/resolved_at.
    """
    body = body or {}
    if not isinstance(body, dict) or not body:
        return None, "nothing to update", 400
    if not is_admin:
        if not is_owner:
            return None, "not found", 404
        if any(k not in OWNER_FIELDS for k in body):
            return None, "only the title and description can be changed", 403
    out: Dict[str, Any] = {}
    for key, value in body.items():
        if key not in ADMIN_FIELDS:
            continue
        if key == "title":
            v = str(value or "").strip()[:TITLE_MAX]
            if not v:
                return None, "title can't be empty", 400
            out[key] = v
        elif key == "description":
            out[key] = str(value or "").strip()[:DESCRIPTION_MAX]
        elif key == "admin_response":
            out[key] = (str(value).strip()[:RESPONSE_MAX] or None) if value is not None else None
        elif key == "status":
            if value not in STATUSES:
                return None, f"status must be one of: {', '.join(STATUSES)}", 400
            out[key] = value
        elif key == "priority":
            if value not in PRIORITIES:
                return None, f"priority must be one of: {', '.join(PRIORITIES)}", 400
            out[key] = value
        elif key == "category":
            if categories is not None and value not in categories:
                return None, "unknown category", 400
            out[key] = value
    if not out:
        return None, "nothing to update", 400
    if "status" in out:
        if out["status"] in CLOSED_STATUSES:
            out["resolved_by"] = actor_id
            out["resolved_at"] = _now()
        else:
            out["resolved_by"] = None
            out["resolved_at"] = None
    return out, None, 200


def visible_to(issue: Optional[Dict[str, Any]], *, is_admin: bool, profile_id,
               follower_ids=()) -> bool:
    """may this caller see the issue at all? the reporter, anyone following
    it, and admins. a foreign id reads as not found."""
    if not issue:
        return False
    if is_admin:
        return True
    try:
        pid = int(profile_id)
        return int(issue.get("profile_id")) == pid or pid in {int(f) for f in follower_ids}
    except (TypeError, ValueError):
        return False


def is_reporter(issue: Optional[Dict[str, Any]], profile_id) -> bool:
    try:
        return bool(issue) and int(issue.get("profile_id")) == int(profile_id)
    except (TypeError, ValueError):
        return False


def strip_reporter(issue: Dict[str, Any]) -> Dict[str, Any]:
    """reporter identity is admin-only."""
    for key in ("reporter_name", "reporter_color", "reporter_avatar"):
        issue.pop(key, None)
    return issue


__all__ = ["STATUSES", "CLOSED_STATUSES", "PRIORITIES", "clean_new_issue", "clean_update",
           "visible_to", "is_reporter", "strip_reporter"]
