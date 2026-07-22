"""Automation handler: ``video_seeding_sweep`` — the torrent seed-until-goals tail.

Thin progress wrapper around :mod:`core.video.seeding` (which owns the overlap
guard, the goal judgment and the client removal)."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_seeding_sweep")


def auto_video_seeding_sweep(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.video.seeding import sweep
    automation_id = config.get("_automation_id")
    deps.update_progress(automation_id, phase="Checking seeding torrents…", progress=10,
                         log_line="Judging completed grabs against your seed goals", log_type="info")
    res = sweep()
    if res.get("status") == "skipped":
        msg = {"already_running": "Previous sweep still running — skipped",
               "no_goals_set": "No seed goals set (Settings → Downloads) — skipping"}.get(
            res.get("reason"), "Skipped")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=msg, log_type="info")
        return {"status": "completed", "released": 0, "skipped": res.get("reason"),
                "_manages_own_progress": True}
    deps.update_progress(
        automation_id, status="finished", progress=100, phase="Complete",
        log_line="%d torrent(s) checked: %d released, %d still seeding"
                 % (res.get("checked", 0), res.get("released", 0), res.get("seeding", 0)),
        log_type="success" if res.get("released") else "info")
    return {"status": "completed", "released": res.get("released", 0),
            "_manages_own_progress": True}
