"""Lenient in-memory failed-attempt limiter for the launch-PIN unlock.

Brute-force protection for a publicly-exposed instance. Deliberately lenient: only
a FLOOD of failures from one client trips it, and a single success clears that
client immediately — so a legitimate user typing their PIN (even with a few typos)
never hits it. Failures age out on their own, so a tripped client self-heals
without any persistent lockout state.

Keyed by client IP. In-memory is fine here: the launch lock is a coarse gate, not
per-account auth, and a process restart simply forgets attempts (fail-open, which
is correct for a self-hosted convenience lock).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple


class AttemptLimiter:
    def __init__(self, max_attempts: int = 10, window_seconds: int = 300):
        """``max_attempts`` failures within ``window_seconds`` → locked until the
        oldest failure in the window ages out."""
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures: Dict[str, List[float]] = defaultdict(list)

    def _prune(self, key: str, now: float) -> List[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def is_locked(self, key: str, now: float) -> Tuple[bool, int]:
        """(locked, retry_after_seconds). retry_after is when the oldest in-window
        failure expires, so the client unlocks naturally."""
        recent = self._prune(key, now)
        if len(recent) >= self.max_attempts:
            retry_after = int(self.window - (now - min(recent))) + 1
            return True, max(retry_after, 1)
        return False, 0

    def record_failure(self, key: str, now: float) -> None:
        self._prune(key, now)
        self._failures[key].append(now)

    def record_success(self, key: str) -> None:
        """A correct entry clears that client's failure history immediately."""
        self._failures.pop(key, None)


__all__ = ["AttemptLimiter"]
