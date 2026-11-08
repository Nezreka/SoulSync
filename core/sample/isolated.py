"""Audio analysis in a child process.

librosa's numba kernels can segfault (seen with Python 3.13 in the beat
tracker); inside the server that took the whole app down with it. One child
answers analysis requests over a line protocol on its own stdin/stdout, so the
imports and JIT warm-up are paid once per batch, and a crash fails only the
request in flight -- the next one starts a fresh child.

The child exits once it has sat idle for ``IDLE_EXIT_S`` (#1938). librosa,
numba and llvm stay resident after an analysis, ~400 MiB that gc and
malloc_trim can't hand back, and analysis is occasional (a bpm backfill, an
import, sample studio). The next request starts a fresh child; its compiled
kernels come off numba's disk cache, so that costs seconds, not a recompile.
``SOULSYNC_ANALYSIS_IDLE_EXIT`` sets the seconds, 0 keeps the child for good.

``multiprocessing`` is deliberately not used: its spawn start method re-imports
the main module in the child, which for ``python web_server.py`` is the app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("sample.isolated")


def _idle_exit_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("SOULSYNC_ANALYSIS_IDLE_EXIT", "300")))
    except ValueError:
        return 300.0


# a batch (a backfill, an import) keeps one child; the gaps between batches don't
IDLE_EXIT_S = _idle_exit_seconds()

_LOCK = threading.Lock()
_CHILD: Optional[subprocess.Popen] = None
_LAST_USED = 0.0
_REAPER: Optional[threading.Timer] = None
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _numba_cache_dir() -> str:
    """where a fresh child finds the kernels the last one compiled. numba's
    default is beside librosa's own files, which a container can't write."""
    return os.environ.get("NUMBA_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "soulsync-numba-cache")


def _child() -> subprocess.Popen:
    global _CHILD
    if _CHILD is None or _CHILD.poll() is not None:
        env = dict(os.environ)
        env["NUMBA_CACHE_DIR"] = _numba_cache_dir()
        _CHILD = subprocess.Popen(
            [sys.executable, "-m", "core.sample.isolated"], cwd=_ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    return _CHILD


def _stop_child_locked() -> None:
    """end the child: closing its stdin ends its read loop, so it exits
    cleanly; one that doesn't is killed. caller holds _LOCK."""
    global _CHILD
    child, _CHILD = _CHILD, None
    if child is None or child.poll() is not None:
        return
    try:
        child.stdin.close()
    except OSError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _arm_reaper_locked(delay: float) -> None:
    global _REAPER
    if IDLE_EXIT_S <= 0 or _REAPER is not None:
        return
    _REAPER = threading.Timer(delay, _reap_if_idle)
    _REAPER.daemon = True
    _REAPER.start()


def _reap_if_idle() -> None:
    global _REAPER
    with _LOCK:
        _REAPER = None
        if _CHILD is None:
            return
        idle = time.monotonic() - _LAST_USED
        if idle >= IDLE_EXIT_S:
            _stop_child_locked()
            logger.info("audio analysis idle %.0fs: its process exited, memory released", idle)
        else:
            # used again since this timer was set: wait out the rest
            _arm_reaper_locked(IDLE_EXIT_S - idle)


def analyze_track_isolated(file_path: str) -> Dict[str, Any]:
    """``analyze_track`` in the child; raises RuntimeError on its failure or crash."""
    global _CHILD, _LAST_USED
    with _LOCK:
        child = _child()
        try:
            child.stdin.write(json.dumps(file_path) + "\n")
            child.stdin.flush()
            line = child.stdout.readline()
        except OSError:
            line = ""
        if not line:
            child.kill()
            _CHILD = None
            raise RuntimeError("audio analysis crashed")
        _LAST_USED = time.monotonic()
        _arm_reaper_locked(IDLE_EXIT_S)
    reply = json.loads(line)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]


def _serve() -> None:
    # Only replies use the real stdout; anything else printed goes to stderr.
    out, sys.stdout = sys.stdout, sys.stderr
    from core.sample.analyze import analyze_track
    for line in sys.stdin:
        try:
            reply = {"result": analyze_track(json.loads(line))}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            reply = {"error": str(exc) or type(exc).__name__}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    _serve()
