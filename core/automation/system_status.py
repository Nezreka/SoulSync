"""When a system automation last ran, and whether it is running at all.

The video calendar says "auto-wishlist ran 2h ago" next to its counts, so a
person can tell a quiet day from an automation that has been off for a week.
The rows live in the app db; ``database=None`` reads it lazily so the video api
can ask without importing the music side (same rule as system_job_schedules).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("automation.system_status")


def _status_of(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    try:
        cfg = json.loads(row.get("trigger_config") or "{}")
    except (TypeError, ValueError):
        cfg = {}
    try:
        result = json.loads(row.get("last_result") or "null")
    except (TypeError, ValueError):
        result = None
    return {
        "enabled": bool(row.get("enabled")),
        "trigger_type": row.get("trigger_type"),
        "interval_hours": cfg.get("interval") if row.get("trigger_type") == "schedule"
        and cfg.get("unit", "hours") == "hours" else None,
        # utc, 'YYYY-MM-DD HH:MM:SS' as the automations table stores them
        "last_run": row.get("last_run"),
        "next_run": row.get("next_run"),
        "last_error": row.get("last_error") or None,
        "last_status": (result or {}).get("status") if isinstance(result, dict) else None,
    }


def system_automation_status(action_types: Iterable[str], database: Any = None) -> Dict[str, Optional[dict]]:
    """``{action_type: {enabled, trigger_type, interval_hours, last_run,
    next_run, last_error, last_status} | None}``. None when the row is missing
    or unreadable; a status line must never fail the page that shows it."""
    out: Dict[str, Optional[dict]] = {}
    try:
        if database is None:
            from database.music_database import get_database
            database = get_database()
    except Exception:  # noqa: BLE001 - no app db, no status
        logger.debug("system automation status: app db unavailable")
        return {a: None for a in action_types}
    for action in action_types:
        try:
            out[action] = _status_of(database.get_system_automation_by_action(action))
        except Exception:  # noqa: BLE001
            out[action] = None
    return out


__all__ = ["system_automation_status"]
