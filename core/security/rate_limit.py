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


    def reset(self) -> None:
        """forget every client (tests, and an admin clearing lockouts)."""
        self._failures.clear()


class TargetedLimiter:
    """failed-attempt limiter keyed by (client, target account).

    AttemptLimiter keyed by ip alone let one success wipe the client's whole
    history: a member with a working password or pin could guess someone
    else's 9 times, sign in as themselves, and go again forever. here a
    success only clears the account that succeeded, and a second per-client
    budget that no success ever clears caps spraying across many accounts.
    """

    def __init__(self, max_attempts: int = 10, window_seconds: int = 300,
                 max_client_attempts: int = 30):
        self._per_target = AttemptLimiter(max_attempts, window_seconds)
        self._per_client = AttemptLimiter(max_client_attempts, window_seconds)

    @staticmethod
    def _key(client: str, target) -> str:
        return f"{client}|{str(target).strip().lower()}"

    def is_locked(self, client: str, target, now: float) -> Tuple[bool, int]:
        locked, retry = self._per_target.is_locked(self._key(client, target), now)
        if locked:
            return locked, retry
        return self._per_client.is_locked(client, now)

    def record_failure(self, client: str, target, now: float) -> None:
        self._per_target.record_failure(self._key(client, target), now)
        self._per_client.record_failure(client, now)

    def record_success(self, client: str, target) -> None:
        self._per_target.record_success(self._key(client, target))

    def reset(self) -> None:
        self._per_target.reset()
        self._per_client.reset()


__all__ = ["AttemptLimiter", "TargetedLimiter"]
