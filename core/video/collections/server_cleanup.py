"""Bulk server-collection deletion as a background job with live progress.

Deleting N collections is N×2 server round-trips — a Kometa cleanup can be
thousands (Boulder's first run: 1,500), which is far too long for one HTTP
request. So the delete endpoint starts THIS job and returns immediately; the
studio follows along over socketio ('collections:cleanup', throttled to ~1/s —
same singleton-job pattern as the overlay apply service) with a status endpoint
as the polling fallback.

Managed collections get their sync-ledger row cleared on successful delete (no
ghost tracking; the next sync recreates them while their definition stays
enabled). Definitions and titles are never touched.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.cleanup")

_JOB = {"running": False, "phase": "idle", "done": 0, "total": 0,
        "deleted": 0, "failed": 0, "name": None, "error": None}
_lock = threading.Lock()

# Live progress over socketio (wired at startup via set_cleanup_progress_emitter).
# Throttled to ~1/s so a huge purge doesn't flood the socket; phase changes
# (start / done / error) always emit so the UI flips state instantly.
_emit: Optional[Callable] = None
_last_emit = [0.0]


def set_cleanup_progress_emitter(fn) -> None:
    global _emit
    _emit = fn


def _emit_progress(force: bool = False) -> None:
    if _emit is None:
        return
    now = time.monotonic()
    if not force and (now - _last_emit[0]) < 1.0:
        return
    _last_emit[0] = now
    try:
        _emit("collections:cleanup", dict(_JOB))
    except Exception:   # noqa: BLE001 - progress is a nicety, never fail the job
        logger.debug("cleanup progress emit failed", exc_info=True)


def status() -> dict:
    """The job's current state (polling fallback for socket-less clients)."""
    return dict(_JOB)


def start_delete(db, ids, *, source=None) -> dict:
    """Start deleting ``ids`` in the background. Returns {ok, total} immediately,
    or {ok: False, error} when a cleanup is already running / no server."""
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return {"ok": False, "error": "ids are required"}
    if source is None:
        from core.video.collections.sync import get_collection_source
        source = get_collection_source()
    if source is None or not hasattr(source, "delete_collection"):
        return {"ok": False, "error": "No video server configured (or it can't do collections)"}
    with _lock:
        if _JOB["running"]:
            return {"ok": False, "error": "a collection cleanup is already running"}
        _JOB.update(running=True, phase="starting", done=0, total=len(ids),
                    deleted=0, failed=0, name=None, error=None)
    _emit_progress(force=True)
    threading.Thread(target=_run, args=(db, ids, source),
                     name="collection-cleanup", daemon=True).start()
    return {"ok": True, "total": len(ids)}


def _run(db, ids, source) -> None:
    try:
        run_delete(db, ids, source)
    except Exception as e:   # noqa: BLE001 - the thread must never die silently
        logger.exception("collection cleanup crashed")
        _JOB.update(phase="error", error=str(e))
    finally:
        _JOB["running"] = False
        _emit_progress(force=True)


def run_delete(db, ids, source) -> dict:
    """The actual per-id delete loop (factored out so tests drive it directly).
    Names are best-effort (from the ledger); one failure never stops the rest."""
    ledger = {}
    try:
        for s in db.list_collection_syncs():
            if s.get("server_source") == source.server_name and s.get("server_id"):
                ledger[str(s["server_id"])] = s
    except Exception:   # noqa: BLE001 - ledger cleanup is secondary to the delete
        logger.debug("ledger read failed for cleanup", exc_info=True)

    _JOB.update(phase="running")
    deleted = failed = 0
    for i, sid in enumerate(ids):
        entry = ledger.get(sid)
        try:
            r = source.delete_collection(sid)
        except Exception as e:   # noqa: BLE001 - keep going; count it
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            deleted += 1
            if entry and entry.get("definition_id") is not None:
                db.delete_collection_sync(entry["definition_id"])
        else:
            failed += 1
            logger.warning("cleanup: delete failed for %s: %s", sid, r.get("error"))
        _JOB.update(done=i + 1, deleted=deleted, failed=failed,
                    name=(entry or {}).get("definition_name"))
        _emit_progress()
    _JOB["phase"] = "done"
    return {"ok": True, "deleted": deleted, "failed": failed}


__all__ = ["start_delete", "run_delete", "status", "set_cleanup_progress_emitter"]
