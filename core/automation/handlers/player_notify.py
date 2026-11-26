"""Automation handler: ``player_notify_library``: the player's notifications.

thin glue around core/player/notify_gen.py, which keeps its own marks so a
run only reads what changed since the last one.
"""

from __future__ import annotations

import threading
from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.player_notify")

_running = threading.Lock()


def is_running() -> bool:
    return _running.locked()


def auto_player_notify(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get("_automation_id")
    if not _running.acquire(blocking=False):
        return {"status": "completed", "skipped": "already_running", "_manages_own_progress": True}
    try:
        from core.player.notify_gen import run_live
        counts = run_live()
    finally:
        _running.release()
    sent = sum(v for k, v in counts.items() if k != "released")
    line = "%d new notification(s), %d let out after quiet hours or a digest" % (sent, counts.get("released", 0))
    deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                         log_line=line, log_type="info")
    return {"status": "completed", "sent": sent, "_manages_own_progress": True}
