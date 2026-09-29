"""Sample Studio analysis worker — one daemon thread, in-memory FIFO queue.

Why not the repair worker: that daemon rotates whole-library sweeps on a
staleness schedule and is disabled by default. Analysis is per-track,
on-demand work ("track imported" / "user opened it in Studio") that must run
whether or not the user enabled library maintenance.

Why an in-memory queue and not a DB queue table: gunicorn runs a single worker
process (workers=1, gthread), so one in-process queue cannot double-run. The
DB row is the idempotency guard — a track already analyzed at the current
ANALYZER_VERSION is skipped, so a restart or duplicate enqueue is harmless.
Anything still pending at shutdown is re-enqueued lazily the next time the
track is opened in Studio (GET /api/v1/sample/analysis enqueues on miss).
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from typing import Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("sample.worker")

_task_queue: "queue.Queue[int]" = queue.Queue()
_pending: set = set()  # track_ids queued or running (dedupe)
_status: Dict[int, str] = {}  # track_id -> pending|running|done|error: ...
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def _resolve_existing_path(stored_path: str) -> Optional[str]:
    """Best-effort: stored DB path -> a file that exists on disk."""
    if stored_path and os.path.isfile(stored_path):
        return stored_path
    try:
        from core.library.path_resolver import resolve_library_file_path

        resolved = resolve_library_file_path(stored_path)
        if resolved and os.path.isfile(resolved):
            return resolved
    except Exception as exc:
        logger.debug("path_resolver failed for %s: %s", stored_path, exc)
    return None


def _process_one(track_id: int) -> None:
    from . import store
    from .analyze import ANALYZER_VERSION, analyze_track

    if store.is_current(track_id):
        return
    stored = store.get_track_file_path(track_id)
    if not stored:
        raise RuntimeError(f"unknown track_id {track_id}")
    path = _resolve_existing_path(stored)
    if not path:
        raise RuntimeError(f"audio file not reachable on disk: {stored}")
    t0 = time.perf_counter()
    result = analyze_track(path)
    store.save_analysis(track_id, result)
    logger.info(
        "Analyzed track %s: %.1f BPM, %d onsets, %.1fs (%.1fs)", track_id, result["bpm"], len(result["onsets"]), result["duration_s"], time.perf_counter() - t0
    )


def _run() -> None:
    while True:
        track_id = _task_queue.get()
        try:
            with _lock:
                _status[track_id] = "running"
            _process_one(int(track_id))
            with _lock:
                _status[track_id] = "done"
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the worker
            logger.warning("Sample analysis failed for track %s: %s", track_id, exc)
            logger.debug(traceback.format_exc())
            with _lock:
                _status[track_id] = f"error: {exc}"
        finally:
            with _lock:
                _pending.discard(int(track_id))
            _task_queue.task_done()


def _ensure_started() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, daemon=True, name="SampleAnalysis")
            _thread.start()
            logger.info("Sample analysis worker started")


def enqueue_analysis(track_id: int) -> str:
    """Queue a track for background analysis. Idempotent; returns the status.

    Never raises — analysis must never break imports or HTTP handlers.
    """
    try:
        track_id = int(track_id)
    except (TypeError, ValueError):
        return "error: invalid track_id"
    try:
        from . import store

        if store.is_current(track_id):
            return "done"
        _ensure_started()
        with _lock:
            if track_id in _pending:
                return _status.get(track_id, "pending")
            _pending.add(track_id)
            _status[track_id] = "pending"
        _task_queue.put(track_id)
        return "pending"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue sample analysis for %s: %s", track_id, exc)
        return f"error: {exc}"


def get_status(track_id: int) -> str:
    """pending|running|done|error: … — 'done' also when already analyzed."""
    try:
        from . import store

        if store.is_current(int(track_id)):
            return "done"
    except Exception as exc:
        logger.debug("status check fell back to queue state: %s", exc)
    with _lock:
        return _status.get(int(track_id), "idle")
