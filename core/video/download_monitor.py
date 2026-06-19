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


def process_download(dl: dict, transfers: list, download_dir: str, *, lister, mover) -> dict | None:
    """Decide the next state for one active download given the current slskd transfers.
    Returns a patch dict for the DB row (or None to leave it untouched this tick)."""
    t = find_transfer(transfers, dl.get("username"), dl.get("filename"))
    if not t:
        return None                                  # not registered yet (or cleared) — wait
    state = classify_state(t.get("state"))
    if state == "active":
        return {"status": "downloading", "progress": progress_pct(t)}
    if state == "failed":
        return {"status": "failed", "error": "Soulseek transfer " + str(t.get("state") or "failed")}
    # completed → locate the file on disk and move it into the library folder
    src = find_completed_file(download_dir, dl.get("filename"), lister)
    if not src:
        return {"progress": 100.0}                   # slskd done; file still settling — retry
    dest = dest_path_for(dl.get("target_dir"), src)
    try:
        mover(src, dest)
    except Exception as e:   # noqa: BLE001 - any move failure marks the download failed
        return {"status": "failed", "error": "Move failed: " + str(e)}
    return {"status": "completed", "progress": 100.0, "dest_path": dest}


def _move(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)


def _walk(root: str):
    for dirpath, _dirs, files in os.walk(str(root or ".")):
        for f in files:
            yield os.path.join(dirpath, f)


def _tick(db) -> None:
    active = db.get_active_video_downloads()
    if not active:
        return
    from config.settings import config_manager
    download_dir = str(config_manager.get("soulseek.download_path", "") or "")
    transfers = list_downloads()
    for dl in active:
        upd = process_download(dl, transfers, download_dir, lister=_walk, mover=_move)
        if not upd:
            continue
        if upd.get("status") in ("completed", "failed"):
            upd.setdefault("completed_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            db.update_video_download(dl["id"], **upd)
        except Exception:
            logger.exception("video download %s: failed to persist update", dl.get("id"))


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
