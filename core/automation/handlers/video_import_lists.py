"""Automation handler: ``video_import_lists`` — recurring external-list sync.

Thin progress wrapper around :mod:`core.video.import_lists` (which owns the
config, the seen-set semantics and the per-source fetchers)."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_import_lists")


def auto_video_import_lists(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.video.import_lists import sync
    automation_id = config.get("_automation_id")
    deps.update_progress(automation_id, phase="Syncing lists…", progress=10,
                         log_line="Reading your import lists", log_type="info")
    lines: list = []
    res = sync(log=lines.append)
    for line in lines[:50]:
        deps.update_progress(automation_id, log_line=line, log_type="info")
    if res.get("status") == "skipped":
        msg = {"already_running": "Previous sync still running — skipped",
               "no_lists": "No import lists configured (Settings → Downloads) — skipping"}.get(
            res.get("reason"), "Skipped")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=msg, log_type="info")
        return {"status": "completed", "added": 0, "skipped": res.get("reason"),
                "_manages_own_progress": True}
    total = res.get("added_movies", 0) + res.get("added_shows", 0)
    line = ("%d list(s) synced: %d movie(s) wishlisted, %d show(s) followed"
            % (res.get("lists", 0), res.get("added_movies", 0), res.get("added_shows", 0)))
    if res.get("requested"):
        line += ", %d sent for approval" % res["requested"]
    if res.get("refused"):
        # the owner's profile can't have them (#2166); they come once it can
        line += ", %d left out by the owner's permissions" % res["refused"]
    deps.update_progress(
        automation_id, status="finished", progress=100, phase="Complete",
        log_line=line, log_type="success" if total or res.get("requested") else "info")
    return {"status": "completed", "added": total, "requested": res.get("requested", 0),
            "_manages_own_progress": True}
