"""How often the status broadcast re-reads a worker's numbers from the database.

The socket loops push every enrichment worker's status every 2 seconds. The
push is cheap; `get_stats()` is not: on the video side it is four COUNT(*)
per kind per worker with nothing kept between ticks, and with a dozen workers
that was 39% of a core for as long as a tab was open (Nov 13 2026, measured).

The beat stays at 2s. What changes is how old the *counts* may be:

- a worker with nothing to do (idle, paused, stopped, not configured) is
  re-read every ``IDLE_TTL`` seconds;
- a worker that is working is re-read no more often than ``ACTIVE_MIN``
  seconds, and less often when its own read is slow, so the read is never
  more than a few percent of the time between reads;
- a start, pause or resume is seen at once, off the worker's in-memory flags;
- what the worker is on right now (``current_item``, ``note``) is always read
  live, so the row in the UI moves on every beat.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

IDLE_TTL = 30.0
ACTIVE_MIN = 5.0
ACTIVE_MAX = 30.0
COST_FACTOR = 25.0
LIVE_FIELDS = ("current_item", "note")


def _flags(worker: Any) -> tuple:
    return (bool(getattr(worker, "running", False)), bool(getattr(worker, "paused", False)))


class WorkerStatsCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        # name -> (read at, what the read cost, the worker's flags then, stats)
        self._entries: Dict[str, Tuple[float, float, tuple, dict]] = {}

    def clear(self) -> None:
        self._entries.clear()

    @staticmethod
    def _ttl(stats: dict, cost: float) -> float:
        if stats.get("idle") or not stats.get("running"):
            return IDLE_TTL
        return max(ACTIVE_MIN, min(ACTIVE_MAX, cost * COST_FACTOR))

    @staticmethod
    def _woke(worker: Any, stats: dict) -> bool:
        """Read as having nothing to do, and now holding an item."""
        return bool(stats.get("idle")) and getattr(worker, "current_item", None) is not None

    def get(self, name: str, worker: Any, fetch: Optional[Callable[[], dict]] = None) -> dict:
        now = self._clock()
        hit = self._entries.get(name)
        if (hit is not None and hit[2] == _flags(worker) and not self._woke(worker, hit[3])
                and now - hit[0] < self._ttl(hit[3], hit[1])):
            stats = dict(hit[3])
            for field in LIVE_FIELDS:
                if field in stats and hasattr(worker, field):
                    stats[field] = getattr(worker, field)
            return stats
        flags = _flags(worker)
        stats = (fetch or worker.get_stats)()
        done = self._clock()
        if isinstance(stats, dict):
            # a copy: the caller adds its own keys to what it is handed
            self._entries[name] = (done, done - now, flags, dict(stats))
        return stats


__all__ = ["WorkerStatsCache", "IDLE_TTL", "ACTIVE_MIN", "ACTIVE_MAX"]
