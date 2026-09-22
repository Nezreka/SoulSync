"""Rolling observed-speed checks shared by Soulseek download paths."""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple


OBSERVED_SPEED_WINDOW_SECONDS = 30.0
OBSERVED_SPEED_LOW_SECONDS = 30.0


class ObservedSpeedTracker:
    """Measure byte movement and identify a sustained low-speed transfer.

    A decision needs one complete sample window, then the average must remain
    below the threshold for a second full low-speed period. Brief dips reset
    that second timer, so an oscillating but healthy transfer is left alone.
    """

    def __init__(self):
        self.samples: Deque[Tuple[float, int]] = deque()
        self.low_since: Optional[float] = None

    def reset(self) -> None:
        self.samples.clear()
        self.low_since = None

    def window_speed(self) -> tuple[Optional[float], float]:
        """Average bytes/second across the retained samples, and their span.

        ``None`` when fewer than two samples exist or no bytes moved, so a
        caller can tell "not measured" apart from "measured at zero".
        """
        if len(self.samples) < 2:
            return None, 0.0
        first_at, first_bytes = self.samples[0]
        last_at, last_bytes = self.samples[-1]
        span = last_at - first_at
        if span <= 0 or last_bytes <= first_bytes:
            return None, span
        return (last_bytes - first_bytes) / span, span

    def observe(self, now: float, transferred: int, minimum_bps: float) -> tuple[Optional[float], bool]:
        transferred = max(0, int(transferred or 0))
        minimum_bps = max(0.0, float(minimum_bps or 0))

        if self.samples and transferred < self.samples[-1][1]:
            # A new/restarted transfer reused the tracker. Its byte counter is
            # unrelated to the previous enqueue, so begin a fresh window.
            self.reset()

        self.samples.append((now, transferred))
        cutoff = now - OBSERVED_SPEED_WINDOW_SECONDS
        # Retain one sample at/before the cutoff so the measured span actually
        # reaches the full window instead of becoming 29 seconds every tick.
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()

        first_at, first_bytes = self.samples[0]
        elapsed = now - first_at
        if elapsed < OBSERVED_SPEED_WINDOW_SECONDS:
            self.low_since = None
            return None, False

        average_bps = max(0.0, (transferred - first_bytes) / elapsed)
        if average_bps >= minimum_bps:
            self.low_since = None
            return average_bps, False

        if self.low_since is None:
            self.low_since = now
        return average_bps, now - self.low_since >= OBSERVED_SPEED_LOW_SECONDS


__all__ = [
    'OBSERVED_SPEED_LOW_SECONDS',
    'OBSERVED_SPEED_WINDOW_SECONDS',
    'ObservedSpeedTracker',
]
