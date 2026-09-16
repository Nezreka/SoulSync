"""Automation handler: ``scan_watchlist_podcasts`` action."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.podcast_automation import scan_and_auto_download_podcasts


def auto_scan_watchlist_podcasts(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Check watchlisted podcast feeds, auto-download the latest episode, and prune expired."""
    try:
        profile_id = config.get('_profile_id', 1)
        res = scan_and_auto_download_podcasts(profile_id=profile_id)
        if not res.get("success"):
            return {
                "status": "error",
                "error": "; ".join(res.get("errors", [])) or "Podcast scan failed",
            }
        return {
            "status": "completed",
            "podcasts_checked": res.get("podcasts_checked", 0),
            "episodes_queued": res.get("episodes_queued", 0),
            "episodes_pruned": res.get("episodes_pruned", 0),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
