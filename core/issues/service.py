"""the issue actions both sides share: report (or follow a duplicate), read
with its thread, triage, reply. music and video each hand in an adapter for
their db and snapshot; every rule lives here once.

returns (payload, http_status) so the flask routes stay one line each.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from core.issues.activity import fix_action, recipients, tell, timeline_events
from core.issues.lifecycle import clean_new_issue, clean_update, is_reporter, strip_reporter, visible_to
from core.issues.thread_store import BODY_MAX

Result = Tuple[Dict[str, Any], int]


@dataclass
class IssueSide:
    get: Callable[[int], Optional[Dict[str, Any]]]
    create: Callable[..., Optional[int]]       # (profile_id, entity_type, entity_id, category, title, description, snapshot, priority, reporter_name) -> id
    update: Callable[[int, Dict[str, Any]], bool]
    store: Any                                  # IssueThreadStore
    notify: Callable[[int, str, str], Any]
    categories: Sequence[str]
    strip_reporter_for_members: bool = False
    side: str = "music"


def _event(side: IssueSide, event_type: str, data: Dict[str, Any]) -> None:
    """issue_created / issue_status_changed / issue_commented for automations."""
    try:
        from core.app_events import publish
        publish(event_type, {"side": side.side, **data})
    except Exception:  # noqa: BLE001, S110 - an automation hook never fails the issue
        pass


def _decorate(side: IssueSide, issue: Dict[str, Any], *, is_admin: bool) -> Dict[str, Any]:
    issue["fix_action"] = fix_action(issue.get("category") or "")
    if not is_admin and side.strip_reporter_for_members:
        strip_reporter(issue)
    return issue


def report(side: IssueSide, *, actor: int, actor_name: str, is_admin: bool, entity_type: str,
           entity_id: str, category: str, body: Dict[str, Any],
           snapshot: Callable[[], Dict[str, Any]]) -> Result:
    """file a report. if the same item already has an open report for the
    same reason, the caller follows that one instead (with their words added
    to its thread), so the admin gets one issue with everyone on it."""
    fields, err = clean_new_issue(body, is_admin=is_admin)
    if err:
        return {"success": False, "error": err}, 400
    dup = side.store.find_open_duplicate(entity_type, str(entity_id), category)
    if dup:
        if is_reporter(dup, actor):
            return {"success": True, "id": dup["id"], "already": True}, 200
        side.store.follow(dup["id"], actor, actor_name)
        words = fields["description"] or fields["title"]
        side.store.add_comment(dup["id"], actor, actor_name, f"Hit this too: {words}")
        return {"success": True, "id": dup["id"], "merged": True}, 200
    new_id = side.create(actor, entity_type, str(entity_id), category, fields["title"],
                         fields["description"], snapshot(), fields["priority"], actor_name)
    if not new_id:
        return {"success": False, "error": "Could not file the report"}, 500
    _event(side, "issue_created", {"issue_id": new_id, "category": category, "title": fields["title"],
                                   "entity_type": entity_type, "reporter": actor_name or ""})
    return {"success": True, "id": new_id}, 201


def detail(side: IssueSide, *, actor: int, is_admin: bool, issue_id: int) -> Result:
    issue = side.get(issue_id)
    if not visible_to(issue, is_admin=is_admin, profile_id=actor,
                      follower_ids=side.store.follower_ids(issue_id) if issue else ()):
        return {"success": False, "error": "Issue not found"}, 404
    if is_reporter(issue, actor) and issue.get("reporter_unread"):
        side.store.set_unread(issue_id, False)
        issue["reporter_unread"] = 0
    issue["comments"] = side.store.list_comments(issue_id)
    issue["followers"] = side.store.followers(issue_id)
    return {"success": True, "issue": _decorate(side, issue, is_admin=is_admin)}, 200


def triage(side: IssueSide, *, actor: int, actor_name: str, is_admin: bool, issue_id: int,
           body: Dict[str, Any]) -> Result:
    """PUT: owners edit their title/description, admins triage. a triage
    change becomes a timeline event and tells the reporter and followers."""
    issue = side.get(issue_id)
    followers = side.store.follower_ids(issue_id) if issue else []
    if not visible_to(issue, is_admin=is_admin, profile_id=actor, follower_ids=followers):
        return {"success": False, "error": "Issue not found"}, 404
    updates, err, code = clean_update(body, is_admin=is_admin, is_owner=is_reporter(issue, actor),
                                      actor_id=actor, categories=side.categories)
    if err:
        return {"success": False, "error": err}, code
    if not side.update(issue_id, updates):
        return {"success": False, "error": "Could not update the issue"}, 500
    if updates.get("status") and updates["status"] != issue.get("status"):
        _event(side, "issue_status_changed", {
            "issue_id": issue_id, "status": updates["status"], "category": issue.get("category") or "",
            "title": issue.get("title") or "", "reporter": issue.get("reporter_name") or ""})
    if is_admin:
        events = timeline_events(issue, updates)
        for line in events:
            side.store.add_comment(issue_id, actor, actor_name, line, kind="event")
        reply = updates.get("admin_response")
        if reply and reply != issue.get("admin_response"):
            side.store.add_comment(issue_id, actor, actor_name, reply)
        if events or (reply and reply != issue.get("admin_response")):
            people = recipients(issue, followers, actor)
            if people:
                side.store.set_unread(issue_id, True)
                what = issue.get("title") or "your report"
                news = events[0] if events else "replied"
                tell(side.notify, people, f"{actor_name or 'An admin'} {news}: {what}",
                     "success" if updates.get("status") == "resolved" else "info")
    return {"success": True}, 200


def comment(side: IssueSide, *, actor: int, actor_name: str, is_admin: bool, issue_id: int,
            body: Dict[str, Any]) -> Result:
    """anyone on the issue can reply: the reporter, a follower, an admin."""
    issue = side.get(issue_id)
    followers = side.store.follower_ids(issue_id) if issue else []
    if not visible_to(issue, is_admin=is_admin, profile_id=actor, follower_ids=followers):
        return {"success": False, "error": "Issue not found"}, 404
    text = str((body or {}).get("body") or "").strip()[:BODY_MAX]
    if not text:
        return {"success": False, "error": "Write something first"}, 400
    cid = side.store.add_comment(issue_id, actor, actor_name, text)
    _event(side, "issue_commented", {"issue_id": issue_id, "title": issue.get("title") or "",
                                     "author": actor_name or "", "body": text[:500]})
    if is_admin:
        people = recipients(issue, followers, actor)
        if people:
            side.store.set_unread(issue_id, True)
            tell(side.notify, people, f"{actor_name or 'An admin'} replied: {issue.get('title')}")
    return {"success": True, "id": cid}, 201


BULK_MAX = 200


def bulk(side: IssueSide, *, actor: int, actor_name: str, is_admin: bool, ids,
         body: Dict[str, Any], delete: Optional[Callable[[int], Any]] = None) -> Result:
    """admin triage of many issues at once: {ids, status?, priority?} or
    {ids, delete: true}. each one goes through the same rules, events and
    notes as a single change; the answer says how many took."""
    if not is_admin:
        return {"success": False, "error": "Admin only"}, 403
    try:
        ids = [int(i) for i in (ids or [])][:BULK_MAX]
    except (TypeError, ValueError):
        return {"success": False, "error": "ids must be numbers"}, 400
    if not ids:
        return {"success": False, "error": "Pick some issues first"}, 400
    changes = {k: body[k] for k in ("status", "priority") if k in (body or {})}
    done, failed = 0, 0
    for iid in ids:
        if (body or {}).get("delete"):
            if delete is None or not side.get(iid):
                failed += 1
                continue
            ok = delete(iid)       # the db's delete_issue takes the thread with it
            ok = ok.get("success") if isinstance(ok, dict) else bool(ok)
        else:
            if not changes:
                return {"success": False, "error": "Nothing to change"}, 400
            _payload, code = triage(side, actor=actor, actor_name=actor_name, is_admin=True,
                                    issue_id=iid, body=dict(changes))
            ok = code == 200
        done, failed = (done + 1, failed) if ok else (done, failed + 1)
    return {"success": True, "done": done, "failed": failed}, 200


__all__ = ["IssueSide", "report", "detail", "triage", "comment", "bulk", "BULK_MAX"]
