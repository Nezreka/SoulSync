"""Automation handler: import ListenBrainz listening history."""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_import_listenbrainz_listening(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    worker = deps.listenbrainz_import_worker
    if worker is None:
        return {"status": "error", "error": "ListenBrainz listening importer is not available"}

    full = bool(config.get("full"))
    result = _import_shared(config, deps, worker, full)

    # every profile with its own listenbrainz keeps its own pile fresh too
    # (#1293). connecting it was the opt-in, the admin's sync switch is about
    # the admin's account and doesn't gate theirs.
    workers = getattr(deps, "listenbrainz_import_workers", None)
    if workers is not None:
        profiles = workers.run_profiles(full=full)
        if profiles:
            result = {**result, "profiles": profiles}
    return result


def _import_shared(config: Dict[str, Any], deps: AutomationDeps, worker, full: bool) -> Dict[str, Any]:
    manual = bool(config.get("_manual_run"))
    enabled = bool(deps.config_manager.get("listenbrainz.listening_sync_enabled", False))
    if not enabled:
        if not manual:
            return {"status": "skipped", "reason": "ListenBrainz listening sync is disabled"}
        deps.config_manager.set("listenbrainz.listening_sync_enabled", True)

    username = config.get("username") or deps.config_manager.get("listenbrainz.username", "")
    return worker.run_once(username=username or None, full=full)
