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

import json
import os
import shutil
import threading
import time

from utils.logging_config import get_logger

from core.video.download_pipeline import dest_path_for, find_completed_file
from core.video.slskd_download import (
    classify_state,
    find_transfer,
    list_downloads,
    progress_pct,
    start_download,
)

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
_db_provider = None      # set by ensure_started; used by the requery worker thread
_requerying: set = set()  # download ids with a requery thread in flight


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── auto-retry ────────────────────────────────────────────────────────────────
def _apply_candidate(db, dl_id, row, cand, rest) -> bool:
    """Start the next candidate download and flip the row back to 'downloading'.
    Returns False if slskd refused to start it."""
    started = start_download(cand.get("username"), cand.get("filename"), cand.get("size_bytes") or 0)
    if not started.get("ok"):
        return False
    tried = []
    try:
        tried = json.loads(row.get("tried_files") or "[]")
    except (ValueError, TypeError):
        tried = []
    tried.append(cand.get("filename"))
    db.update_video_download(
        dl_id, status="downloading", progress=0, error=None, completed_at=None,
        username=cand.get("username"), filename=cand.get("filename"),
        release_title=cand.get("release_title") or cand.get("filename"),
        size_bytes=int(cand.get("size_bytes") or 0), quality_label=cand.get("quality_label"),
        candidates=json.dumps(rest), tried_files=json.dumps(tried),
        attempts=int(row.get("attempts") or 0) + 1)
    _misses.pop(dl_id, None)
    return True


def _fail_or_retry(db, dl, error_msg) -> None:
    """A download just failed/disappeared. Try the next candidate inline; if none,
    hand off to a requery thread; if nothing left, mark it failed for real."""
    from core.video.retry import plan_retry
    plan = plan_retry(dl)
    if plan["action"] == "candidate" and _apply_candidate(db, dl["id"], dl, plan["candidate"], plan["rest"]):
        return
    if plan["action"] in ("candidate", "requery"):
        db.update_video_download(dl["id"], status="searching", error=None)
        _spawn_requery(dl["id"])
        return
    db.update_video_download(dl["id"], status="failed", error=error_msg or "Download failed",
                             completed_at=_now())


def _search_for_retry(query, max_seconds=22):
    """A bounded blocking slskd search for the retry worker (shorter than the UI's)."""
    from core.video.slskd_search import poll_search, start_search
    res = start_search(query)
    sid = res.get("id")
    if not sid:
        return {"hits": [], "total_files": 0}
    deadline = time.monotonic() + max_seconds
    last = {"hits": [], "total_files": 0}
    while time.monotonic() < deadline:
        last = poll_search(sid)
        if len(last.get("hits") or []) >= 12:
            break
        time.sleep(1.5)
    return last


def _requery_worker(dl_id) -> None:
    from core.video.quality_eval import evaluate_release
    from core.video.quality_profile import load as load_profile
    from core.video.release_parse import parse_release
    from core.video.retry import merge_candidates, plan_retry
    try:
        db = _db_provider() if _db_provider else None
        if db is None:
            return
        profile = load_profile(db)
        for _ in range(8):   # hard loop cap on top of the attempt budget
            row = db.get_video_download(dl_id)
            if not row or row.get("status") != "searching":
                return
            plan = plan_retry(row)
            if plan["action"] == "candidate":
                if _apply_candidate(db, dl_id, row, plan["candidate"], plan["rest"]):
                    return
                continue
            if plan["action"] != "requery":
                break
            query, ctx = plan["query"], plan.get("ctx") or {}
            # record the attempt + the query we're about to try
            tq = []
            try:
                tq = json.loads(row.get("tried_queries") or "[]")
            except (ValueError, TypeError):
                tq = []
            tq.append(query)
            db.update_video_download(dl_id, tried_queries=json.dumps(tq),
                                     attempts=int(row.get("attempts") or 0) + 1)
            polled = _search_for_retry(query)
            accepted = []
            for hit in (polled.get("hits") or []):
                v = evaluate_release(parse_release(hit.get("title")), profile,
                                     scope=ctx.get("scope") or "movie",
                                     want_season=ctx.get("season"), want_episode=ctx.get("episode"))
                if v["accepted"]:
                    accepted.append(hit)
            row2 = db.get_video_download(dl_id)
            if not row2 or row2.get("status") != "searching":
                return
            tried_files = []
            try:
                tried_files = json.loads(row2.get("tried_files") or "[]")
            except (ValueError, TypeError):
                tried_files = []
            fresh = merge_candidates(accepted, tried_files)
            if fresh and _apply_candidate(db, dl_id, row2, fresh[0], fresh[1:]):
                return
            # this query gave nothing usable → loop tries the next query (or fails)
        db.update_video_download(dl_id, status="failed",
                                 error="No working release found after retries", completed_at=_now())
    except Exception:
        logger.exception("video download %s: requery worker failed", dl_id)
        try:
            if _db_provider:
                _db_provider().update_video_download(dl_id, status="failed",
                                                     error="Retry error", completed_at=_now())
        except Exception:
            logger.exception("video download %s: could not mark failed", dl_id)
    finally:
        _requerying.discard(dl_id)


def _spawn_requery(dl_id) -> None:
    if dl_id in _requerying:
        return
    _requerying.add(dl_id)
    threading.Thread(target=_requery_worker, args=(dl_id,), daemon=True,
                     name="video-dl-requery-%s" % dl_id).start()


def _tick(db) -> None:
    # 'searching' rows are owned by their requery thread — skip them here.
    active = [d for d in db.get_active_video_downloads() if d.get("status") != "searching"]
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
            n = _misses.get(dl["id"], 0) + 1
            _misses[dl["id"]] = n
            if n >= _GIVE_UP_AFTER:
                _misses.pop(dl["id"], None)
                _fail_or_retry(db, dl, "Soulseek transfer disappeared")
            continue
        _misses.pop(dl["id"], None)
        if upd.get("status") == "failed":
            _fail_or_retry(db, dl, upd.get("error"))      # auto-retry before truly failing
            continue
        if upd.get("status") in ("completed", "cancelled"):
            upd.setdefault("completed_at", _now())
        try:
            db.update_video_download(dl["id"], **upd)
        except Exception:
            logger.exception("video download %s: failed to persist update", dl.get("id"))
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
    global _started, _db_provider
    with _lock:
        _db_provider = db_provider
        if _started:
            return
        _started = True
        threading.Thread(target=_run, args=(db_provider,), daemon=True,
                         name="video-download-monitor").start()


__all__ = ["process_download", "ensure_started"]
