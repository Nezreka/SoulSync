"""The two bins a music library leaves behind, and one sweep for both.

quarantine (``<download_path>/ss_quarantine``) holds downloads that failed
verification and are waiting on a decision. the recycle bin (the deleted
quarantine under the transfer folder) holds library files a tool deleted,
kept so they can be reclaimed. neither empties itself: the quarantine never
did, and the recycle bin's retention only ever ran when someone opened the
Recycle Bin tab, so a bin nobody looked at grew forever (LettuceSnob, Sep 17
2026: "is there any way for me to manually trigger that scrub myself?").

this module is the single place that knows how to empty each one. the api
route, the clear_quarantine automation, full_cleanup and the weekly
library_cleanup automation all call in here instead of carrying their own
copy of the folder walk.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library.cleanup")

QUARANTINE_DIRNAME = "ss_quarantine"


def quarantine_path(download_path: str) -> str:
    return os.path.join(download_path, QUARANTINE_DIRNAME)


def clear_quarantine_folder(path: str) -> Dict[str, Any]:
    """Delete everything directly under ``path``. Files and folders both
    count as one item, as they always have. A missing folder is an empty
    one. Never raises for one bad entry: it is reported and the rest still
    go. Returns ``{'existed', 'removed', 'errors': [{'entry', 'error'}]}``."""
    if not os.path.isdir(path):
        return {"existed": False, "removed": 0, "errors": []}
    removed = 0
    errors: List[Dict[str, str]] = []
    for entry in os.listdir(path):
        fp = os.path.join(path, entry)
        try:
            if os.path.isfile(fp) or os.path.islink(fp):
                os.remove(fp)
                removed += 1
            elif os.path.isdir(fp):
                shutil.rmtree(fp)
                removed += 1
        except Exception as exc:  # noqa: BLE001 - best-effort purge, keep going
            logger.debug("quarantine entry purge failed for %s: %s", entry, exc)
            errors.append({"entry": entry, "error": str(exc)})
    return {"existed": True, "removed": removed, "errors": errors}


def purge_recycle_bin(transfer_folder: str, keep_days: float) -> Dict[str, Any]:
    """Empty the deleted quarantine the way its retention setting says to.

    keep_days > 0: only entries older than that (the same age rule the
    Recycle Bin tab applies on open). keep_days == 0 means "keep forever"
    on the tab, but an automation someone switched on is an instruction to
    empty the bin, so 0 empties it. Returns ``{'mode', 'keep_days',
    'purged', 'errors'}``."""
    from core.library import deleted_quarantine

    try:
        days = float(keep_days or 0)
    except (TypeError, ValueError):
        days = 0.0
    if days > 0:
        purged = deleted_quarantine.purge_expired(transfer_folder, days)
        return {"mode": "expired", "keep_days": days, "purged": int(purged or 0), "errors": []}
    result = deleted_quarantine.purge_entries(transfer_folder, None, purge_all=True)
    return {"mode": "all", "keep_days": 0.0,
            "purged": len(result.get("purged") or []),
            "errors": list(result.get("errors") or [])}


def run_library_cleanup(*, download_path: str, transfer_folder: str, keep_days: float,
                        clear_quarantine: bool = True, empty_recycle_bin: bool = True,
                        say: Optional[Any] = None) -> Dict[str, Any]:
    """The weekly sweep: quarantine, then the recycle bin, each optional.
    ``say(line, kind)`` narrates a step when given. Returns a summary with
    both step results, the total removed and every error, and never raises
    for one step so the other still runs."""
    summary: Dict[str, Any] = {"quarantine": None, "recycle_bin": None, "removed": 0, "errors": []}

    def _say(line: str, kind: str = "info") -> None:
        if say is None:
            return
        try:
            say(line, kind)
        except Exception as exc:  # noqa: BLE001 - narration never breaks the sweep
            logger.debug("cleanup narration failed: %s", exc)

    if clear_quarantine:
        try:
            q = clear_quarantine_folder(quarantine_path(download_path))
        except Exception as exc:  # noqa: BLE001
            logger.exception("library cleanup: quarantine step failed")
            q = {"existed": True, "removed": 0, "errors": [{"entry": "", "error": str(exc)}]}
        summary["quarantine"] = q
        summary["removed"] += q["removed"]
        summary["errors"].extend({"step": "quarantine", **e} for e in q["errors"])
        _say("Quarantine: removed %d item(s)" % q["removed"] if q["existed"]
             else "Quarantine: no folder yet, nothing to clear",
             "success" if q["removed"] else "info")

    if empty_recycle_bin:
        try:
            r = purge_recycle_bin(transfer_folder, keep_days)
        except Exception as exc:  # noqa: BLE001
            logger.exception("library cleanup: recycle bin step failed")
            r = {"mode": "error", "keep_days": keep_days, "purged": 0,
                 "errors": [{"id": "", "error": str(exc)}]}
        summary["recycle_bin"] = r
        summary["removed"] += r["purged"]
        summary["errors"].extend({"step": "recycle_bin", **e} for e in r["errors"])
        if r["mode"] == "expired":
            _say("Recycle bin: purged %d file(s) older than %g day(s)" % (r["purged"], r["keep_days"]),
                 "success" if r["purged"] else "info")
        elif r["mode"] == "all":
            _say("Recycle bin: emptied, %d file(s) deleted for good" % r["purged"],
                 "success" if r["purged"] else "info")
        else:
            _say("Recycle bin: step failed, see log", "error")

    return summary
