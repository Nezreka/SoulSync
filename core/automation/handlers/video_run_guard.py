"""Overlap guard for video automation actions.

A manual "Run now" overlapping the nightly timer doubles the TMDB bill; a
>2h refresh overlapping the 01:00 airing read means the airing run covers days
from a partially-refreshed calendar and advances the bookmark (the same
permanent-miss shape as the stale-refresh hole). The engine calls guard_fn()
with no args; True means "busy, skip this run".
"""
from __future__ import annotations

import time


class VideoRunGuard:
    """True (busy) while a run is in progress and not stuck.

    The stuck timeout lets a crashed run (process killed without clearing the
    flag) be retried instead of blocking the action forever.
    """

    def __init__(self, timeout_seconds: int = 7200):
        self._started_at: float | None = None
        self._timeout = timeout_seconds

    def __call__(self) -> bool:
        if self._started_at is None:
            return False
        if time.time() - self._started_at > self._timeout:
            return False  # stuck — allow a new run
        return True

    def __enter__(self):
        self._started_at = time.time()
        return self

    def __exit__(self, *args):
        self._started_at = None
        return False
