"""Automation handler: import Last.fm listening history."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_import_lastfm_listening(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    worker = deps.lastfm_import_worker
    if worker is None:
        return {"status": "error", "error": "Last.fm listening importer is not available"}

    full = bool(config.get("full"))
    result = _import_shared(config, deps, worker, full)

    # every profile with its own last.fm keeps its own history fresh too
    # (#1293). saving the username was the opt-in, the admin's sync switch is
    # about the admin's account and doesn't gate theirs.
    workers = getattr(deps, "lastfm_import_workers", None)
    if workers is not None:
        profiles = workers.run_profiles(full=full)
        if profiles:
            result = {**result, "profiles": profiles}
    return result


def _import_shared(config: Dict[str, Any], deps: AutomationDeps, worker, full: bool) -> Dict[str, Any]:
    manual = bool(config.get("_manual_run"))
    enabled = bool(deps.config_manager.get("lastfm.listening_sync_enabled", False))
    if not enabled:
        if not manual:
            return {"status": "skipped", "reason": "Last.fm listening sync is disabled"}
        deps.config_manager.set("lastfm.listening_sync_enabled", True)

    username = config.get("username") or deps.config_manager.get("lastfm.username", "")
    return worker.run_once(username=username or None, full=full)
