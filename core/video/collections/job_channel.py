"""Shared singleton-job progress channel.

The collection jobs (server cleanup, sync-all, artwork refresh) all follow the
same pattern: one job at a time, a state dict the UI polls, and live progress
over a socket event throttled to ~1/s (start/finish always emit so the UI
flips state instantly). This is that pattern, once — each job module keeps its
own public surface and delegates the concurrency/emit mechanics here.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.jobs")


class JobChannel:
    """One background job's state + throttled socket progress."""

    def __init__(self, event: str, idle_fields: dict):
        self.event = event
        self._idle = dict(idle_fields)
        self.job = {"running": False, "phase": "idle", **self._idle}
        self._lock = threading.Lock()
        self._emit: Optional[Callable] = None
        self._last_emit = 0.0

    def set_emitter(self, fn) -> None:
        self._emit = fn

    def status(self) -> dict:
        return dict(self.job)

    def emit(self, force: bool = False) -> None:
        if self._emit is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < 1.0:
            return
        self._last_emit = now
        try:
            self._emit(self.event, dict(self.job))
        except Exception:   # noqa: BLE001 - progress is a nicety, never fail the job
            logger.debug("%s progress emit failed", self.event, exc_info=True)

    def acquire(self, **start_fields) -> bool:
        """Claim the job (False when one is running) and emit the start state."""
        with self._lock:
            if self.job["running"]:
                return False
            self.job.update(running=True, phase="starting", **self._idle)
            self.job.update(start_fields)
        self.emit(force=True)
        return True

    def release(self) -> None:
        """Mark not-running and emit the final state (call from finally)."""
        self.job["running"] = False
        self.emit(force=True)


__all__ = ["JobChannel"]
