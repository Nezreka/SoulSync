"""Automation handler: ``library_cleanup`` action.

The weekly sweep behind the seeded "Weekly Cleanup" automation (off by
default): clear the download quarantine, then empty the recycle bin per its
retention setting. Both steps are switches in the action config so one
automation can do either half on its own.

Why this exists: the quarantine had an action but nothing scheduled it, and
the recycle bin's retention only ever ran when someone opened the Recycle Bin
tab. LettuceSnob (Sep 17 2026) asked for a way to trigger the scrub; this is
the switch. The sweep itself lives in core.library.cleanup, this is the
schedule plus progress.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.library.cleanup import run_library_cleanup
from utils.logging_config import get_logger

logger = get_logger("automation.library_cleanup")

RETENTION_KEY = "library.deleted_keep_days"


def _flag(config: Dict[str, Any], key: str) -> bool:
    """Absent = on: a seeded row has an empty config and must do both halves."""
    value = config.get(key)
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def auto_library_cleanup(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Returns ``{'status': 'completed', 'removed': int, 'quarantine_removed':
    int, 'recycle_purged': int, 'errors': int}``."""
    automation_id = config.get("_automation_id")
    do_quarantine = _flag(config, "quarantine")
    do_recycle = _flag(config, "recycle_bin")
    if not do_quarantine and not do_recycle:
        deps.update_progress(automation_id, log_line="Nothing selected: both steps are switched off",
                             log_type="info")
        return {"status": "skipped", "reason": "both steps switched off"}

    cfg = deps.config_manager
    download_path = deps.docker_resolve_path(cfg.get("soulseek.download_path", "./downloads"))
    transfer_folder = deps.docker_resolve_path(cfg.get("soulseek.transfer_path", "./Transfer"))
    try:
        keep_days = float(cfg.get(RETENTION_KEY, 0) or 0)
    except (TypeError, ValueError):
        keep_days = 0.0

    def _say(line: str, kind: str) -> None:
        deps.update_progress(automation_id, log_line=line, log_type=kind)

    try:
        deps.update_progress(automation_id, phase="Cleaning up…", progress=10)
        summary = run_library_cleanup(
            download_path=download_path, transfer_folder=transfer_folder, keep_days=keep_days,
            clear_quarantine=do_quarantine, empty_recycle_bin=do_recycle, say=_say,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("library cleanup automation failed")
        deps.update_progress(automation_id, log_line=str(exc), log_type="error")
        return {"status": "error", "error": str(exc)}

    q = summary.get("quarantine") or {}
    r = summary.get("recycle_bin") or {}
    errors = summary.get("errors") or []
    result = {
        "status": "completed",
        "removed": int(summary.get("removed") or 0),
        "quarantine_removed": int(q.get("removed") or 0),
        "recycle_purged": int(r.get("purged") or 0),
        "errors": len(errors),
    }
    logger.info("library cleanup: %s", result)
    return result
