"""Automation handler: import Last.fm listening history."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_import_lastfm_listening(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    worker = deps.lastfm_import_worker
    if worker is None:
        return {"status": "error", "error": "Last.fm listening importer is not available"}

    manual = bool(config.get("_manual_run"))
    enabled = bool(deps.config_manager.get("lastfm.listening_sync_enabled", False))
    if not enabled:
        if not manual:
            return {"status": "skipped", "reason": "Last.fm listening sync is disabled"}
        deps.config_manager.set("lastfm.listening_sync_enabled", True)

    username = config.get("username") or deps.config_manager.get("lastfm.username", "")
    return worker.run_once(username=username or None, full=bool(config.get("full")))