"""Scan existing audiobook files through the shared automation engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_scan_audiobook_library(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.audiobook_organizer import library_root
    from core.audiobook_database import subsystem_in_use
    from core.audiobook_library_scan import scan

    automation_id = config.get("_automation_id")
    root = str(config.get("root") or library_root())
    # A configured folder is enough to discover an existing library on first use.
    if not subsystem_in_use() and not Path(root).is_dir():
        return {"status": "completed", "skipped": "No audiobook folder available yet"}

    def progress(state):
        running = state["status"] == "running"
        failed = state["status"] == "error"
        total = max(1, state.get("found", 0))
        phase = (f"Reading {state.get('current', 'audiobook folders')}" if running else
                 state.get("error") if failed else
                 f"Library updated: {state['adopted']} added, {state['updated']} refreshed, {state['removed']} missing")
        deps.update_progress(
            automation_id, status="running" if running else "error" if failed else "finished",
            progress=min(95, int(state["checked"] / total * 95)) if running else 100,
            phase=phase, log_line=phase, log_type="error" if failed else "info")

    try:
        result = scan(root=root, progress=progress)
        return {**result, "_manages_own_progress": result.get("status") != "skipped"}
    except Exception as exc:
        deps.update_progress(automation_id, status="error", phase="Scan failed",
                             log_line=str(exc), log_type="error")
        return {"status": "error", "error": str(exc), "_manages_own_progress": True}
