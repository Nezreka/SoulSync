"""Sample Studio stems worker — daemon threads, in-memory FIFO queues.

Same shape as the analysis worker (worker.py): gunicorn runs a single worker
process, so one in-process queue cannot double-run. The DB rows are the
idempotency guard — a track whose outputs exist at the current
SEPARATOR_VERSION is skipped, so a restart or duplicate enqueue is harmless.

jobs are keyed (track_id, method) and each method gets its own queue, so a
future quick method never waits behind a multi-minute demucs job. the UI polls
GET /api/v1/sample/stems/status.

Deliberately NOT the repair sweep framework (wrong model — see worker.py).
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Dict, Optional, Tuple

from utils.logging_config import get_logger

from .ids import track_key

logger = get_logger("sample.stems_worker")

_LANES = ("demucs",)
_queues: Dict[str, "queue.Queue[Tuple[str, Optional[str], str]]"] = {
    lane: queue.Queue() for lane in _LANES
}
_pending: set = set()  # (track_id, method) queued or running (dedupe)
_status: Dict[Tuple[str, str], str] = {}  # -> queued|running|done|error: ...
_last_method: Dict[str, str] = {}  # the method the page asked for most recently
_progress: Dict[Tuple[str, str], float] = {}  # 0..1 while a job runs
_lock = threading.Lock()
_threads: Dict[str, threading.Thread] = {}

# kept for callers/tests that poke the old single queue
_task_queue = _queues["demucs"]


def _lane(method: str) -> str:
    return method if method in _LANES else "demucs"


def _process_one(track_id: str, backend_name: Optional[str] = None, method: str = "demucs") -> None:
    from . import stems as stems_mod
    from . import store

    if store.stems_complete(track_id, method):
        return
    backend = stems_mod.get_separator(method, backend_name)
    key = (track_key(track_id), method)

    def _report(fraction: float) -> None:
        with _lock:
            _progress[key] = max(0.0, min(1.0, float(fraction)))

    t0 = time.perf_counter()
    paths = stems_mod.separate_track(track_id, backend=backend, method=method, progress=_report)
    logger.info(
        "Separated track %s with %s: %d outputs (%.0fs)",
        track_id, backend.name, len(paths), time.perf_counter() - t0,
    )


def _run(lane: str) -> None:
    q = _queues[lane]
    while True:
        track_id, backend_name, method = q.get()
        key = (track_id, method)
        try:
            with _lock:
                _status[key] = "running"
            _process_one(track_key(track_id), backend_name, method)
            with _lock:
                _status[key] = "done"
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the worker
            logger.warning("Stem separation (%s) failed for track %s: %s", method, track_id, exc)
            logger.debug(traceback.format_exc())
            with _lock:
                _status[key] = f"error: {exc}"
        finally:
            with _lock:
                _pending.discard(key)
                _progress.pop(key, None)
            q.task_done()


def _ensure_started(lane: str) -> None:
    with _lock:
        t = _threads.get(lane)
        if t is None or not t.is_alive():
            t = threading.Thread(target=_run, args=(lane,), daemon=True,
                                 name=f"SampleStems-{lane}")
            _threads[lane] = t
            t.start()
            logger.info("Sample stems worker (%s) started", lane)


def enqueue_separation(track_id: str, backend: Optional[str] = None,
                       method: str = "demucs") -> str:
    """Queue a track for separation with `method`. Idempotent; returns the status.

    Never raises — separation must never break HTTP handlers.
    """
    try:
        track_id = track_key(track_id)
    except (TypeError, ValueError):
        return "error: invalid track_id"
    try:
        from . import store
        from .stems import SEPARATION_METHODS

        if method not in SEPARATION_METHODS:
            return f"error: unknown separation method {method!r}"
        with _lock:
            _last_method[track_id] = method
        if store.stems_complete(track_id, method):
            return "done"
        lane = _lane(method)
        _ensure_started(lane)
        key = (track_id, method)
        with _lock:
            if key in _pending:
                return _status.get(key, "queued")
            _pending.add(key)
            _status[key] = "queued"
        _queues[lane].put((track_id, backend, method))
        return "queued"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue stem separation for %s: %s", track_id, exc)
        return f"error: {exc}"


def current_method(track_id: str) -> str:
    """the method the page is looking at: the last one asked for, else the
    first that already has results, else demucs."""
    with _lock:
        last = _last_method.get(track_key(track_id))
    if last:
        return last
    try:
        from . import store
        from .stems import SEPARATION_METHODS

        for method in SEPARATION_METHODS:
            if store.stems_complete(track_key(track_id), method):
                return method
    except Exception as exc:
        logger.debug("stems method lookup failed: %s", exc)
    return "demucs"


def get_status(track_id: str, method: Optional[str] = None) -> str:
    """queued|running|done|error: … — 'done' also when outputs already exist."""
    method = method or current_method(track_id)
    try:
        from . import store

        if store.stems_complete(track_key(track_id), method):
            return "done"
    except Exception as exc:
        logger.debug("stems status check fell back to queue state: %s", exc)
    with _lock:
        return _status.get((track_key(track_id), method), "idle")


def get_progress(track_id: str, method: Optional[str] = None) -> Optional[float]:
    """0..1 while that job runs, None otherwise."""
    method = method or current_method(track_id)
    with _lock:
        return _progress.get((track_key(track_id), method))


def queue_depth() -> int:
    return sum(q.qsize() for q in _queues.values())
