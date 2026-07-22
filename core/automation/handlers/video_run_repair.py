"""Automation handler: ``video_run_repair_job`` action.

Force-runs Library Maintenance jobs from an automation — so users can chain
repair after a scan ("Video Database Updated → run YouTube Ghost Files →
Discord me the findings") or put a job on a cadence the Tools-page interval
can't express (monthly, after-downloads, ...). Queuing rides the repair
worker's existing force-run queue: one job at a time, overlap-safe, findings
land on the Tools page exactly like a manual ▶.

``job_id`` config: one job id, or ``all`` = every ENABLED job (respects the
per-job toggle; disabled jobs are never run behind the user's back).
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_run_repair")


def auto_video_run_repair_job(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.video.repair.worker import get_video_repair_worker
    worker = get_video_repair_worker()
    want = str((config or {}).get("job_id") or "all").strip()

    jobs = worker.get_all_job_info()
    known = {j["job_id"]: j for j in jobs}
    if want in ("all", "all_due"):
        targets = [j["job_id"] for j in jobs if j.get("enabled")]
        if not targets:
            return {"status": "skipped", "queued": 0, "jobs": "",
                    "reason": "No maintenance jobs are enabled"}
    elif want in known:
        targets = [want]          # explicit pick runs even if its toggle is off
    else:
        return {"status": "error", "error": f"unknown maintenance job '{want}'"}

    queued = [j for j in targets if worker.run_job_now(j)]
    skipped = [j for j in targets if j not in queued]
    if skipped:
        logger.info("repair automation: %s already running/queued", ", ".join(skipped))
    return {"status": "completed", "queued": len(queued), "jobs": ", ".join(queued),
            "skipped": len(skipped),
            "summary": f"Queued {len(queued)} maintenance job(s)"}
