"""Collection sync-all as a job with live progress (bell + studio).

Same singleton pattern as the overlay apply service and the cleanup job:
one job at a time, state in ``_JOB``, progress over the 'collections:sync'
socket event (throttled to ~1/s; start/finish always emit) with a status
endpoint as the polling fallback.

Two entry points share the lock so a manual run and the nightly automation can
never overlap:
  * ``start_sync_all`` — background thread (the studio's "Sync all" button).
  * ``sync_all_with_progress`` — synchronous (the daily automation), still
    feeding ``_JOB``/socket so the bell shows the nightly run live too.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.sync_job")

_JOB = {"running": False, "phase": "idle", "done": 0, "total": 0,
        "synced": 0, "failed": 0, "added": 0, "removed": 0, "wishlisted": 0,
        "name": None, "error": None}
_lock = threading.Lock()

_emit: Optional[Callable] = None
_last_emit = [0.0]


def set_sync_progress_emitter(fn) -> None:
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
        _emit("collections:sync", dict(_JOB))
    except Exception:   # noqa: BLE001 - progress is a nicety, never fail the sync
        logger.debug("sync progress emit failed", exc_info=True)


def status() -> dict:
    """The job's current state (polling fallback / bell seed)."""
    return dict(_JOB)


def _acquire() -> bool:
    with _lock:
        if _JOB["running"]:
            return False
        _JOB.update(running=True, phase="starting", done=0, total=0, synced=0,
                    failed=0, added=0, removed=0, wishlisted=0, name=None, error=None)
    _emit_progress(force=True)
    return True


def sync_all_with_progress(db, *, force: bool = False,
                           on_progress: Optional[Callable] = None) -> dict:
    """Run the full sync SYNCHRONOUSLY while feeding the job state/socket —
    the daily automation's path, so the nightly run lights up the bell exactly
    like a manual one. Returns run_sync's result dict; refuses to overlap."""
    if not _acquire():
        return {"ok": False, "error": "a collection sync is already running"}
    return _execute(db, force=force, on_progress=on_progress)


def _execute(db, *, force: bool, on_progress: Optional[Callable]) -> dict:
    """The run body — the caller must hold the acquired job (via _acquire)."""
    try:
        from core.video.collections.sync import run_sync

        def _prog(done, total, name):
            _JOB.update(done=done, total=total, phase="running", name=name)
            _emit_progress()
            if on_progress:
                try:
                    on_progress(done, total, name)
                except Exception:   # noqa: BLE001 - a caller's progress hook can't kill the run
                    pass

        res = run_sync(db, force=force, on_progress=_prog)
        if res.get("ok"):
            _JOB.update(phase="done", done=_JOB["total"],
                        synced=res.get("synced", 0), failed=res.get("failed", 0),
                        added=res.get("added", 0), removed=res.get("removed", 0),
                        wishlisted=res.get("wishlisted", 0))
        else:
            _JOB.update(phase="error", error=res.get("error") or "sync failed")
        return res
    except Exception as e:   # noqa: BLE001 - report, never raise into the caller
        logger.exception("collection sync run crashed")
        _JOB.update(phase="error", error=str(e))
        return {"ok": False, "error": str(e)}
    finally:
        _JOB["running"] = False
        _emit_progress(force=True)


def start_sync_all(db, *, force: bool = False) -> dict:
    """Start the full sync in the background (the studio's 'Sync all').
    Returns {ok} immediately, or {ok: False, error} when one is running.
    Acquires BEFORE spawning so a double-click can't race two runs."""
    if not _acquire():
        return {"ok": False, "error": "a collection sync is already running"}
    threading.Thread(target=lambda: _execute(db, force=force, on_progress=None),
                     name="collection-sync", daemon=True).start()
    return {"ok": True, "started": True}


__all__ = ["start_sync_all", "sync_all_with_progress", "status", "set_sync_progress_emitter"]
