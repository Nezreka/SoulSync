"""Automation handler: ``video_extto_fresh_refresh`` — keep Fresh Releases warm.

Thin progress/glue wrapper around :mod:`core.video.extto_board`, which owns the
pull, the detail matching and the cache. The Refresh button on Search → Fresh
Releases calls the same function, so a scheduled run and a user-initiated one do
identical work and leave identical state.

Runs hourly by default: that is roughly how fast the EXT.to board turns over, and
because matched releases are cached by detail URL, an hourly cadence costs almost
nothing after the first run.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_extto_fresh")


def auto_video_extto_fresh_refresh(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from api.video import get_video_db
    from core.video.extto_board import MAX_NEW_DETAILS_PER_RUN, refresh_board

    automation_id = config.get("_automation_id")

    def _log(line: str) -> None:
        deps.update_progress(automation_id, log_line=line, log_type="info")

    def _tick(pct: int, phase: str) -> None:
        deps.update_progress(automation_id, phase=phase, progress=pct)

    try:
        cap = int(config.get("max_new_details") or MAX_NEW_DETAILS_PER_RUN)
    except (TypeError, ValueError):
        cap = MAX_NEW_DETAILS_PER_RUN

    deps.update_progress(automation_id, phase="Refreshing Fresh Releases…", progress=5,
                         log_line="Pulling the EXT.to board", log_type="info")
    res = refresh_board(get_video_db(), log=_log, progress=_tick, max_new_details=max(1, cap))

    if res.get("status") == "skipped":
        reason = {
            "already_running": "A refresh is already running — skipped",
            "not_configured": "EXT.to needs FlareSolverr — set flaresolverr.url (Settings)",
        }.get(res.get("reason"), "Skipped")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=reason, log_type="info")
        return {"status": "completed", "skipped": res.get("reason"), "_manages_own_progress": True}

    if not res.get("ok"):
        err = res.get("error") or "The EXT.to board could not be refreshed"
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=err, log_type="error")
        return {"status": "failed", "error": err, "_manages_own_progress": True}

    summary = "%d release(s): %d from cache, %d newly matched" % (
        res.get("rows", 0), res.get("cached", 0), res.get("fetched", 0))
    if res.get("skipped"):
        summary += ", %d deferred to the next run" % res["skipped"]
    deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                         log_line=summary, log_type="success")
    return {"status": "completed", "rows": res.get("rows", 0),
            "matched": res.get("fetched", 0), "_manages_own_progress": True}
