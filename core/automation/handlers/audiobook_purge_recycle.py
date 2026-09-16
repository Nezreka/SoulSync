"""Automation handler: ``audiobook_purge_recycle`` action.

Empties the audiobook recycle bin of anything past ``recycle_keep_days``.

The schedule is what matters, not the opportunistic pass: deletes only happen
when somebody deletes something, so on a library nobody is pruning the bin
would never expire and would quietly hold every book ever removed.

Deliberately NOT tagged owned_by, matching the other audio automations.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def _fmt_gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 3):.2f} GB"


def auto_purge_audiobook_recycle(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Unlink recycled books past the keep window."""
    try:
        from core.audiobook_database import subsystem_in_use

        if not subsystem_in_use():
            return {"status": "completed", "skipped": "no audiobooks yet"}

        from core.audiobook_recycle import purge_old

        summary = purge_old(days=config.get("keep_days"))
        return {
            "status": "completed",
            "removed": summary.get("removed", 0),
            "kept": summary.get("kept", 0),
            "freed": _fmt_gb(summary.get("freed_bytes", 0)),
        }
    except Exception as exc:                                # noqa: BLE001
        return {"status": "error", "error": str(exc)}
