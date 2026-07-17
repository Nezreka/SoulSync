"""Automation handler: ``video_rss_sync`` — RSS-speed grabbing via Prowlarr.

Thin progress/glue wrapper around :mod:`core.video.rss_sync` (which owns the
overlap guard and all acquisition seams). Runs every few minutes; each tick is
one aggregate recent-releases pull matched against the eligible wishlist.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_rss_sync")


def auto_video_rss_sync(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.video.rss_sync import rss_pass
    automation_id = config.get("_automation_id")
    deps.update_progress(automation_id, phase="Checking indexers…", progress=10,
                         log_line="Pulling recent releases from Prowlarr", log_type="info")

    lines: list = []
    res = rss_pass(log=lines.append)

    for line in lines:
        deps.update_progress(automation_id, log_line=line, log_type="info")

    if res.get("status") == "skipped":
        reason = {
            "already_running": "Previous RSS tick still running — skipped",
            "prowlarr_not_configured": "Prowlarr not configured — skipping (Settings → Downloads)",
            "no_indexer_source_enabled": "No torrent/usenet source in your download mode — skipping",
        }.get(res.get("reason"), "Skipped")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=reason, log_type="info")
        return {"status": "completed", "grabbed": 0, "skipped": res.get("reason"),
                "_manages_own_progress": True}

    grabbed = res.get("grabbed", 0)
    matched = res.get("matched_items", 0)
    # 'match' here = a wishlist title had a namesake in the feed; the per-item
    # 'RSS skip:' lines above say why any matched-but-not-grabbed one didn't
    # qualify (wrong quality/scope, or already-owned upgrade-only).
    summary = "%d release(s) in the feed, %d name-match(es), %d grabbed" % (
        res.get("releases", 0), matched, grabbed)
    if matched and not grabbed:
        summary += " — see the per-title reasons above"
    deps.update_progress(
        automation_id, status="finished", progress=100, phase="Complete",
        log_line=summary, log_type="success" if grabbed else "info")
    return {"status": "completed", "grabbed": grabbed, "_manages_own_progress": True}
