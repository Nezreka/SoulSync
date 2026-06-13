"""Stall detection for the database-update job.

The DB updater keeps a single in-memory state dict whose ``status`` is set to
``running`` at start and only flipped to ``finished``/``error`` by the worker's
completion/error callbacks. If the worker thread hangs — e.g. a media-server API
call with no timeout, a DB lock — those callbacks never fire, so ``status`` stays
``running`` forever and the UI shows a frozen progress bar with no way to recover
(GitHub #859).

This module is the single, *pure* decision for "is a running job stalled?". It
takes the state dict plus the current wall-clock time and a timeout, and answers
yes/no — no DB, no globals, no clock of its own. That keeps it unit-testable and
lets the watchdog wiring in web_server.py stay a thin call. The job carries a
``last_progress_at`` epoch timestamp that the start path and every progress/phase
callback bump; staleness is simply "running, and that timestamp is older than the
timeout".
"""

from __future__ import annotations

from typing import Any, Mapping

# 5 minutes with zero forward progress = presumed hung. A healthy scan ticks
# progress (per-artist) far more often than this even for large libraries, so
# the timeout won't false-positive a slow-but-working run.
DEFAULT_STALL_TIMEOUT_SECONDS = 300


def is_db_update_stalled(
    state: Mapping[str, Any],
    now: float,
    timeout_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS,
) -> bool:
    """Return True when the job is ``running`` but has made no progress within
    ``timeout_seconds``.

    Conservative by design — it only ever reports a stall it can prove:
    - Only a ``running`` job can stall (idle/finished/error never do).
    - With no usable ``last_progress_at`` timestamp we cannot judge, so we return
      False rather than risk killing a job we have no clock for.
    - A non-positive timeout is treated as "disabled" (never stalls).
    """
    if not isinstance(state, Mapping):
        return False
    if state.get("status") != "running":
        return False
    if timeout_seconds is None or timeout_seconds <= 0:
        return False
    last = state.get("last_progress_at")
    if not last:
        return False
    try:
        elapsed = float(now) - float(last)
    except (TypeError, ValueError):
        return False
    return elapsed >= float(timeout_seconds)


def stalled_error_message(state: Mapping[str, Any], now: float) -> str:
    """Build a clear, human-facing message for a stalled job, including how long
    it has been silent and the phase it died in."""
    last = state.get("last_progress_at") if isinstance(state, Mapping) else None
    phase = state.get("phase") if isinstance(state, Mapping) else None
    try:
        secs = int(float(now) - float(last)) if last else 0
    except (TypeError, ValueError):
        secs = 0
    msg = "Update appears stuck — no progress"
    if secs > 0:
        msg += f" for {secs}s"
    if phase:
        msg += f" (last phase: {phase})"
    msg += (". The worker may be hung on the media server. Start a new update "
            "to try again, or restart SoulSync if it keeps stalling.")
    return msg


__all__ = [
    "DEFAULT_STALL_TIMEOUT_SECONDS",
    "is_db_update_stalled",
    "stalled_error_message",
]
