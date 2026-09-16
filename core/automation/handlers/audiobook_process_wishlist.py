"""Automation handler: ``audiobook_process_wishlist`` action.

The audiobook wishlist is drained by the shared automation engine, the same way
music's ``process_wishlist`` and video's ``video_process_movie_wishlist`` are —
not by a private timer thread. That matters for more than tidiness: an
automation is visible on the Automations page, can be paused, rescheduled or run
by hand, and is subject to the master switch. A background thread is none of
those things, and a user who wanted it to stop would have no way to say so.

Deliberately NOT tagged owned_by: the podcast scan isn't either, so audio-side
automations land on the same page as music rather than being filtered off it.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_process_audiobook_wishlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Search for wishlisted audiobooks that are due, and grab what turns up.

    One pass over the books whose per-book backoff has expired — the same
    function the "Search now" button on the wishlist page calls, so the manual
    and scheduled paths cannot drift apart.

    Skips quietly on an install that has never used audiobooks: the subsystem's
    database is created on first real use, and seeding an automation must not be
    what brings it into existence.
    """
    try:
        from core.audiobook_database import subsystem_in_use

        if not subsystem_in_use():
            return {"status": "completed", "skipped": "no audiobooks wishlisted yet"}

        from core.audiobook_wishlist_worker import run_pass

        limit = config.get("batch_size")
        summary = run_pass(limit=int(limit) if limit else None)
        return {
            "status": "completed",
            "checked": summary.get("checked", 0),
            "found": summary.get("found", 0),
            "grabbed": summary.get("grabbed", 0),
            "errors": summary.get("errors", 0),
        }
    except Exception as exc:                                # noqa: BLE001
        return {"status": "error", "error": str(exc)}
