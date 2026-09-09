"""Automation handler: ``audiobook_scan_watchlist`` action.

Followed authors are checked on a schedule by the shared automation engine, the
same way podcasts are by ``scan_watchlist_podcasts`` and artists by
``scan_watchlist``. Daily rather than hourly: an audiobook is announced weeks
ahead and published on a date, so checking more often spends effort to learn
nothing.

Deliberately NOT tagged owned_by, matching the podcast scan — audio-side
automations belong on the same page as music.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_scan_audiobook_watchlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Check followed authors and wishlist anything they have released since.

    Skips quietly on an install that has never used audiobooks: the subsystem's
    database is created on first real use, and seeding an automation must not be
    what brings it into existence.
    """
    try:
        from core.audiobook_database import subsystem_in_use

        if not subsystem_in_use():
            return {"status": "completed", "skipped": "no audiobook authors followed yet"}

        from core.audiobook_watchlist import run_scan

        limit = config.get("batch_size")
        summary = run_scan(limit=int(limit) if limit else None)
        return {
            "status": "completed",
            "authors_checked": summary.get("authors", 0),
            "releases_found": summary.get("found", 0),
            "wishlisted": summary.get("wishlisted", 0),
            "errors": summary.get("errors", 0),
        }
    except Exception as exc:                                # noqa: BLE001
        return {"status": "error", "error": str(exc)}
