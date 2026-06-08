"""On-demand memory-growth diagnostic (issue #802: ~0.7 MiB/s RSS growth).

Wraps ``tracemalloc`` so a user seeing runaway memory can capture WHERE the
allocations come from instead of us guessing:

    1. start_tracking()  — begins tracing + stores a baseline snapshot
    2. ...reproduce the growth for a few minutes...
    3. report()          — top allocation sites, with the DELTA since baseline
                           (the delta is the leak; absolute sizes are mostly
                           startup noise)
    4. stop_tracking()   — ends tracing, frees trace memory

Opt-in by design: tracemalloc costs CPU and memory while active (it shadows
every allocation), so it must never run by default. The Flask endpoints that
expose this live in web_server (GET /api/debug/memory/...) so a user can drive
the whole flow from a browser.
"""

from __future__ import annotations

import os
import time
import tracemalloc
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("diagnostics.memory")

_baseline: Optional[tracemalloc.Snapshot] = None
_started_at: Optional[float] = None

# Allocation-site traces this deep give useful "who called it" context without
# pathological overhead.
_TRACE_FRAMES = 15


def is_tracking() -> bool:
    return tracemalloc.is_tracing()


def start_tracking() -> Dict[str, Any]:
    """Begin tracing and store the baseline snapshot. Idempotent."""
    global _baseline, _started_at
    if tracemalloc.is_tracing():
        return {"tracking": True, "already_running": True, "started_at": _started_at}
    tracemalloc.start(_TRACE_FRAMES)
    _baseline = tracemalloc.take_snapshot()
    _started_at = time.time()
    logger.info("Memory tracking started (tracemalloc, %d frames)", _TRACE_FRAMES)
    return {"tracking": True, "already_running": False, "started_at": _started_at}


def stop_tracking() -> Dict[str, Any]:
    """End tracing and free the trace bookkeeping."""
    global _baseline, _started_at
    was = tracemalloc.is_tracing()
    if was:
        tracemalloc.stop()
        logger.info("Memory tracking stopped")
    _baseline = None
    _started_at = None
    return {"tracking": False, "was_tracking": was}


def _rss_mb() -> Optional[float]:
    """Process RSS in MiB, best-effort (psutil, then /proc fallback)."""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: S110 — RSS is optional context; fall through to /proc
        pass
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:  # noqa: S110 — no /proc on this platform; RSS stays None
        pass
    return None


def format_stat(stat: Any) -> Dict[str, Any]:
    """Project one tracemalloc StatisticDiff/Statistic into a plain dict.
    Duck-typed (reads size/count/size_diff/count_diff/traceback) so it's
    unit-testable without real snapshots."""
    tb = getattr(stat, "traceback", None)
    frames: List[str] = []
    if tb:
        # Most-recent-call-last reads naturally top-down in a report.
        for frame in list(tb)[-3:]:
            frames.append(f"{frame.filename}:{frame.lineno}")
    return {
        "location": frames[-1] if frames else "?",
        "trace": frames,
        "size_mb": round(getattr(stat, "size", 0) / (1024 * 1024), 3),
        "size_diff_mb": round(getattr(stat, "size_diff", 0) / (1024 * 1024), 3),
        "count": getattr(stat, "count", 0),
        "count_diff": getattr(stat, "count_diff", 0),
    }


def report(top: int = 25) -> Dict[str, Any]:
    """Current snapshot vs the start_tracking() baseline: the top allocation
    sites by GROWTH (size_diff). Includes traced totals + process RSS so the
    user can see how much of the real growth tracemalloc accounts for."""
    if not tracemalloc.is_tracing():
        return {
            "tracking": False,
            "rss_mb": _rss_mb(),
            "hint": "Start with /api/debug/memory/start, reproduce the growth "
                    "for a few minutes, then call this again.",
        }
    snapshot = tracemalloc.take_snapshot()
    # Filter the tracer's own bookkeeping out of the picture.
    snapshot = snapshot.filter_traces((
        tracemalloc.Filter(False, tracemalloc.__file__),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    ))
    current, peak = tracemalloc.get_traced_memory()

    if _baseline is not None:
        stats = snapshot.compare_to(_baseline, "traceback")
        stats.sort(key=lambda s: s.size_diff, reverse=True)
    else:
        stats = snapshot.statistics("traceback")

    return {
        "tracking": True,
        "started_at": _started_at,
        "elapsed_seconds": round(time.time() - _started_at, 1) if _started_at else None,
        "traced_current_mb": round(current / (1024 * 1024), 1),
        "traced_peak_mb": round(peak / (1024 * 1024), 1),
        "rss_mb": _rss_mb(),
        "top_growth": [format_stat(s) for s in stats[:top]],
    }
