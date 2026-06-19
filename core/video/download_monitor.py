"""Background monitor that drives video downloads to completion.

A daemon thread polls slskd for the active video downloads, updates their progress,
and when one finishes MOVES the file from the shared download folder into the right
per-type library folder (Movies / TV / YouTube) and marks it completed. Simple v1:
slskd source only, flat move by basename.

The per-download decision (``process_download``) is pure — filesystem + slskd are
injected — so it's unit-tested; the thread loop is thin glue.

Isolated: stdlib + the sibling video modules + shared config_manager; no music imports.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

from utils.logging_config import get_logger

from core.video.download_pipeline import dest_path_for, find_completed_file
from core.video.slskd_download import classify_state, find_transfer, list_downloads, progress_pct

logger = get_logger("video.download_monitor")

_INTERVAL = 3            # seconds between polls
_started = False
_lock = threading.Lock()


def _complete_via_file(dl, download_dir, lister, mover):
    """Locate the finished file in the download dir and move it to the library. Returns
    a completed/failed patch, or {'progress':100} if the file isn't there yet."""
    src = find_completed_file(download_dir, dl.get("filename"), lister)
    if not src:
        return {"progress": 100.0}
    dest = dest_path_for(dl.get("target_dir"), src)
    try:
        mover(src, dest)
    except Exception as e:   # noqa: BLE001 - any move failure marks the download failed
        return {"status": "failed", "error": "Move failed: " + str(e)}
    return {"status": "completed", "progress": 100.0, "dest_path": dest}


def process_download(dl: dict, transfers: list, download_dir: str, *, lister, mover) -> dict | None:
    """Decide the next state for one active download given the current slskd transfers.
    Returns a patch dict for the DB row, or {'_missing': True} when slskd no longer
    knows the transfer (the caller decides when to give up). Robust to slskd clearing
    completed transfers (the music 'Clean Completed Downloads' automation) by also
    detecting completion from the file landing on disk."""
    t = find_transfer(transfers, dl.get("username"), dl.get("filename"))
    if not t:
        # slskd forgot it — could be done+cleared. If the file's there, finish it.
        done = _complete_via_file(dl, download_dir, lister, mover)
        if done.get("status"):
            return done
        return {"_missing": True}
    state = classify_state(t.get("state"))
    if state == "active":
        return {"status": "downloading", "progress": progress_pct(t)}
    if state == "cancelled":
        return {"status": "cancelled", "error": "Cancelled on Soulseek"}
    if state == "failed":
        return {"status": "failed", "error": "Soulseek transfer " + str(t.get("state") or "failed")}
    return _complete_via_file(dl, download_dir, lister, mover)   # completed


def _move(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)


def _walk(root: str):
    for dirpath, _dirs, files in os.walk(str(root or ".")):
        for f in files:
            yield os.path.join(dirpath, f)


_GIVE_UP_AFTER = 8       # consecutive 'transfer gone, no file' polls before failing it
_misses: dict = {}       # download id -> consecutive missing polls


def _tick(db) -> None:
    active = db.get_active_video_downloads()
    if not active:
        _misses.clear()
        return
    from config.settings import config_manager
    download_dir = str(config_manager.get("soulseek.download_path", "") or "")
    transfers = list_downloads()
    live_ids = set()
    for dl in active:
        live_ids.add(dl["id"])
        upd = process_download(dl, transfers, download_dir, lister=_walk, mover=_move)
        if not upd:
            continue
        if upd.get("_missing"):
            # slskd no longer has the transfer and the file never appeared. Give it a
            # few polls (a just-cancelled transfer vanishes), then mark it failed so it
            # doesn't sit on 'downloading' forever.
            n = _misses.get(dl["id"], 0) + 1
            _misses[dl["id"]] = n
            if n >= _GIVE_UP_AFTER:
                _misses.pop(dl["id"], None)
                db.update_video_download(dl["id"], status="failed",
                                         error="Soulseek transfer disappeared",
                                         completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            continue
        _misses.pop(dl["id"], None)
        if upd.get("status") in ("completed", "failed", "cancelled"):
            upd.setdefault("completed_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            db.update_video_download(dl["id"], **upd)
        except Exception:
            logger.exception("video download %s: failed to persist update", dl.get("id"))
    # drop miss counters for ids that are no longer active
    for k in [k for k in _misses if k not in live_ids]:
        _misses.pop(k, None)


def _run(db_provider) -> None:
    logger.info("video download monitor started")
    while True:
        try:
            db = db_provider()
            if db is not None:
                _tick(db)
        except Exception:
            logger.exception("video download monitor tick failed")
        time.sleep(_INTERVAL)


def ensure_started(db_provider) -> None:
    """Start the monitor thread once (idempotent). Called when the first grab happens."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_run, args=(db_provider,), daemon=True,
                         name="video-download-monitor").start()


__all__ = ["process_download", "ensure_started"]
