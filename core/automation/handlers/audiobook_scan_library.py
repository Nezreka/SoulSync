"""Automation handler: ``audiobook_scan_library`` action.

Reconciles the audiobook library table against the folder on disk, so an Owned
badge never outlives the book it describes. Daily, in the quiet hours after the
other audio automations have had their turn.

The music side's ``scan_library`` reads a whole music library and enriches it;
this is much smaller on purpose. Audiobooks have no library page to populate,
so the only thing the record is for is answering "do I already have this", and
the only way it goes wrong is the user moving or deleting a folder.

Deliberately NOT tagged owned_by, matching the other audio automations.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_scan_audiobook_library(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Forget books that are gone and adopt folders that turned up.

    Skips quietly on an install that has never used audiobooks: seeding an
    automation must not be what creates the subsystem's database.
    """
    try:
        from core.audiobook_database import subsystem_in_use

        if not subsystem_in_use():
            return {"status": "completed", "skipped": "no audiobooks yet"}

        from core.audiobook_library_scan import scan

        summary = scan(root=config.get("root") or None)

        if summary.get("missing_root"):
            # An unmounted share, almost always. Reported rather than treated
            # as an empty library, because forgetting everything over it would
            # be unrecoverable.
            return {"status": "completed",
                    "skipped": "the audiobook folder is not reachable"}

        return {
            "status": "completed",
            "checked": summary.get("checked", 0),
            "removed": summary.get("removed", 0),
            "adopted": summary.get("adopted", 0),
            "updated": summary.get("updated", 0),
        }
    except Exception as exc:                                # noqa: BLE001
        return {"status": "error", "error": str(exc)}
