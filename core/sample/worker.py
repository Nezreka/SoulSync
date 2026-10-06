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

_task_queue: "queue.Queue[object]" = queue.Queue()
_pending: set = set()  # (library scope, track_id) queued or running
_status: Dict[tuple, str] = {}  # (scope, track_id) -> pending|running|done|error
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None

# Injected by configure() at boot (web_server.py). The path resolver needs
# it to read library.music_paths / transfer paths: without it the
# container<->host fallback below has no base directories to walk against
# and every non-literal stored path silently fails to resolve.
_config_manager = None

# the web server's playback resolver (_resolve_library_file_path). injected so
# sample studio finds a file exactly where the player does: same bases, plus
# plex all-libraries locations and the confusable-tolerant suffix scan (#833)
# the shared resolver doesn't have.
_resolve_path_fn = None

# The DSP warmup is kicked once at boot so the first analyzed track doesn't
# stall on the librosa import + numba JIT cold start.
_warmup_started = False


def _warm_dsp_safe() -> None:
    from .analyze import warm_dsp

    warm_dsp()


def configure(*, config_manager_=None, resolve_path_fn=None, warm=True) -> None:
    """Inject shared services. Safe to call more than once."""
    global _config_manager, _resolve_path_fn, _warmup_started
    _config_manager = config_manager_
    _resolve_path_fn = resolve_path_fn
    _resolve_cache.clear()
    if warm and not _warmup_started:
        _warmup_started = True
        threading.Thread(
            target=_warm_dsp_safe, daemon=True, name="SampleDSPWarmup"
        ).start()


def resolve_audio_path(stored_path: str) -> Optional[str]:
    """Best-effort: stored DB path -> a file that exists on disk.

    Tries the raw path first, then the shared library path resolver
    (suffix-walk against the configured music/transfer folders), which is
    what translates container-style stored paths (``/mnt/musicBackup/…``)
    to the host layout on native installs and vice versa.

    when the web server injected its playback resolver, that goes first, so a
    track that plays also chops. the shared resolver is the fallback for
    boot paths that never configured us (it still reads the global config).
    """
    if stored_path and os.path.isfile(stored_path):
        return stored_path
    if _resolve_path_fn is not None:
        try:
            resolved = _resolve_path_fn(stored_path)
            if resolved and os.path.isfile(resolved):
                return resolved
        except Exception as exc:
            logger.debug("playback resolver failed for %s: %s", stored_path, exc)
    try:
        from core.library.path_resolver import resolve_library_file_path

        config = _config_manager
        if config is None:
            from core.settings import config_manager as config
        resolved = resolve_library_file_path(stored_path, config_manager=config)
        if resolved and os.path.isfile(resolved):
            return resolved
    except Exception as exc:
        logger.debug("path_resolver failed for %s: %s", stored_path, exc)
    return None


# Backwards-compatible alias (core/sample/stems.py imports the old name).
_resolve_existing_path = resolve_audio_path

# stored path -> resolved path. status polls and the stale-file check hit
# this every few seconds, and a miss can mean a suffix scan over the whole
# library, so hits are kept for a bit (and re-checked on disk before use).
_RESOLVE_TTL_S = 60.0
_resolve_cache: Dict[str, tuple] = {}


def resolve_track_file(stored_path: str) -> Optional[str]:
    """resolve_audio_path with a short memory. only hits are remembered."""
    if not stored_path:
        return None
    hit = _resolve_cache.get(stored_path)
    if hit and time.monotonic() - hit[0] < _RESOLVE_TTL_S and os.path.isfile(hit[1]):
        return hit[1]
    resolved = resolve_audio_path(stored_path)
    if resolved:
        _resolve_cache[stored_path] = (time.monotonic(), resolved)
    else:
        _resolve_cache.pop(stored_path, None)
    return resolved


def unreachable_message(stored_path: str) -> str:
    """what to tell someone whose track plays but can't be chopped.

    playback can stream a track from the media server when the file isn't on
    this machine. sample studio can't, it needs the actual file.
    """
    return (
        f"SoulSync can't open this file on disk ({stored_path}). Playback can "
        "stream it from your media server, but Sample Studio needs the file "
        "itself. Add the folder that holds your music under Settings > Library "
        "so SoulSync can reach it."
    )


def track_source(track_id: int) -> tuple:
    """(resolved_path, signature) for a library track, (None, None) when unreachable."""
    from . import store

    stored = store.get_track_file_path(track_id)
    path = resolve_track_file(stored) if stored else None
    return path, (store.source_signature(path) if path else None)


def _process_one(track_id: int) -> None:
    from . import store
    from .isolated import analyze_track_isolated

    if store.is_current(track_id):
        return
    stored = store.get_track_file_path(track_id)
    if not stored:
        raise RuntimeError(f"unknown track_id {track_id}")
    path = resolve_track_file(stored)
    if not path:
        raise RuntimeError(unreachable_message(stored))
    t0 = time.perf_counter()
    result = analyze_track_isolated(path)
    store.save_analysis(track_id, result, source_sig=store.source_signature(path))
    logger.info(
        "Analyzed track %s: %.1f BPM, %d onsets, %.1fs (%.1fs)", track_id, result["bpm"], len(result["onsets"]), result["duration_s"], time.perf_counter() - t0
    )


def _job_key(track_id: int) -> tuple:
    from core.library_scope import current_library_scope

    return current_library_scope(), int(track_id)


def _run() -> None:
    while True:
        queued = _task_queue.get()
        if isinstance(queued, tuple):
            track_id, scope = queued
            has_scope = True
        else:
            track_id, scope = queued, None
            has_scope = False
        token = None
        key = None
        try:
            if has_scope:
                from core.library_scope import set_library_scope
                token = set_library_scope(scope)
            key = _job_key(track_id)
            with _lock:
                _status[key] = "running"
            _process_one(int(track_id))
            with _lock:
                _status[key] = "done"
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the worker
            logger.warning("Sample analysis failed for track %s: %s", track_id, exc)
            logger.debug(traceback.format_exc())
            with _lock:
                if key is not None:
                    _status[key] = f"error: {exc}"
        finally:
            if token is not None:
                from core.library_scope import reset_library_scope
                reset_library_scope(token)
            with _lock:
                _pending.discard(key)
            _task_queue.task_done()


def _ensure_started() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, daemon=True, name="SampleAnalysis")
            _thread.start()
            logger.info("Sample analysis worker started")


def enqueue_analysis(track_id: int, retry: bool = False) -> str:
    """Queue a track for background analysis. Idempotent; returns the status.

    Never raises — analysis must never break imports or HTTP handlers.

    Error statuses are STICKY: once the worker records ``"error: …"``, further
    enqueues return it unchanged instead of silently re-queueing. The old
    behavior reset the status to ``"pending"`` on every status poll, so a
    failure (e.g. librosa not installed) was unobservable — the client polled
    every 2.5s, each poll re-queued the track, and the UI spun on
    "Analyzing…" forever. Pass ``retry=True`` to clear a recorded error and
    queue the track again (the Studio "Try again" button).
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
        key = _job_key(track_id)
        with _lock:
            current = _status.get(key)
            if current and current.startswith("error"):
                if not retry:
                    return current
                # Explicit retry: clear the recorded failure so the track
                # actually re-queues instead of deduping onto the error.
                _status.pop(key, None)
                _pending.discard(key)
            if key in _pending:
                return _status.get(key, "pending")
            _pending.add(key)
            _status[key] = "pending"
        _task_queue.put((track_id, key[0]))
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
        return _status.get(_job_key(track_id), "idle")
