"""Automation handler: ``video_purge_recycle_bin`` action.

Empties the video recycle bin of anything past ``recycle_keep_days``
(Settings, Library Organization).

Why this exists: purge_old only ever ran inside discard(), so the bin was
swept as a side effect of deleting the NEXT file. stop deleting things and
nothing expires, the keep-days setting quietly does nothing and the bin grows
forever. that is what people hit. now a daily pass owns it.

Pure-ish: the purge itself lives in core.video.recycle, this is the schedule
plus progress.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_purge_recycle")


def _fmt_gb(b: int) -> str:
    """Same wording as the YouTube retention job."""
    gb = (b or 0) / (1024 ** 3)
    return ("%.1f GB" % gb) if gb >= 0.1 else "%d MB" % round((b or 0) / (1024 ** 2))


def _default_purge():
    """(count, bytes_freed) for the real bin."""
    from api.video import get_video_db
    from core.video import organization, recycle
    db = get_video_db()
    return recycle.purge_old_detailed(organization.load(db), db)


def auto_video_purge_recycle_bin(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    purge: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """Delete recycled files past the keep window. Returns
    ``{'status': 'completed', 'removed': int, 'freed_bytes': int, ...}``."""
    purge = purge or _default_purge
    automation_id = config.get("_automation_id")
    try:
        deps.update_progress(automation_id, phase="Emptying the recycle bin…", progress=20,
                             log_line="Looking for recycled files past their keep window",
                             log_type="info")
        removed, freed = purge() or (0, 0)
        removed, freed = int(removed or 0), int(freed or 0)
        msg = ("Deleted %d expired file(s) from the recycle bin, freed %s"
               % (removed, _fmt_gb(freed))) if removed \
            else "Nothing expired, the recycle bin is within its keep window"
        logger.info("recycle purge automation: %s", msg)
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=msg, log_type="success" if removed else "info")
        return {"status": "completed", "removed": removed, "freed_bytes": freed,
                "_manages_own_progress": True}
    except Exception as e:  # noqa: BLE001
        logger.exception("recycle purge automation failed")
        deps.update_progress(automation_id, status="error", phase="Error",
                             log_line=str(e), log_type="error")
        return {"status": "error", "error": str(e), "_manages_own_progress": True}
