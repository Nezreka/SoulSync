"""Sample Studio stems worker — one daemon thread, in-memory FIFO queue.

Same shape as the analysis worker (worker.py): gunicorn runs a single worker
process, so one in-process queue cannot double-run. The DB rows are the
idempotency guard — a track whose four stems exist at the current
SEPARATOR_VERSION is skipped, so a restart or duplicate enqueue is harmless.
A separation takes minutes on CPU; the queue is strictly FIFO, one track at a
time, and the UI polls GET /api/v1/sample/stems/status for progress.

Deliberately NOT the repair sweep framework (wrong model — see worker.py).
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("sample.stems_worker")

_task_queue: "queue.Queue[int]" = queue.Queue()
_pending: set = set()  # track_ids queued or running (dedupe)
_status: Dict[int, str] = {}  # track_id -> queued|running|done|error: ...
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def _process_one(track_id: int, backend_name: Optional[str] = None) -> None:
    from . import stems as stems_mod
    from . import store

    if store.stems_complete(track_id):
        return
    backend = stems_mod.get_backend(backend_name)
    t0 = time.perf_counter()
    paths = stems_mod.separate_track(track_id, backend=backend)
    logger.info(
        "Separated track %s with %s: %d stems (%.0fs)",
        track_id, backend.name, len(paths), time.perf_counter() - t0,
    )


def _run() -> None:
    while True:
        track_id, backend_name = _task_queue.get()
        try:
            with _lock:
                _status[track_id] = "running"
            _process_one(int(track_id), backend_name)
            with _lock:
                _status[track_id] = "done"
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the worker
            logger.warning("Stem separation failed for track %s: %s", track_id, exc)
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
            _thread = threading.Thread(target=_run, daemon=True, name="SampleStems")
            _thread.start()
            logger.info("Sample stems worker started")


def enqueue_separation(track_id: int, backend: Optional[str] = None) -> str:
    """Queue a track for stem separation. Idempotent; returns the status.

    Never raises — separation must never break HTTP handlers.
    """
    try:
        track_id = int(track_id)
    except (TypeError, ValueError):
        return "error: invalid track_id"
    try:
        from . import store

        if store.stems_complete(track_id):
            return "done"
        _ensure_started()
        with _lock:
            if track_id in _pending:
                return _status.get(track_id, "queued")
            _pending.add(track_id)
            _status[track_id] = "queued"
        _task_queue.put((track_id, backend))
        return "queued"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue stem separation for %s: %s", track_id, exc)
        return f"error: {exc}"


def get_status(track_id: int) -> str:
    """queued|running|done|error: … — 'done' also when stems already exist."""
    try:
        from . import store

        if store.stems_complete(int(track_id)):
            return "done"
    except Exception as exc:
        logger.debug("stems status check fell back to queue state: %s", exc)
    with _lock:
        return _status.get(int(track_id), "idle")


def queue_depth() -> int:
    with _lock:
        return _task_queue.qsize()
