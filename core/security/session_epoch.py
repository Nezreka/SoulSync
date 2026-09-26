"""sign a profile out everywhere.

sessions are signed cookies, so the server can't reach out and delete one.
instead each profile row carries a session_epoch and each session remembers
the epoch it was signed in under; bumping the row's epoch makes every older
session a session with no profile. a session from before this existed has no
epoch and counts as 0, so upgrading signs nobody out.

the row is read on every request of a signed-in browser, so it's cached for a
few seconds per process; the bump clears this process's copy at once.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Tuple

_TTL = 5.0
_cache: Dict[int, Tuple[float, int]] = {}
_lock = threading.Lock()


def _epoch(pid: int, load: Callable[[int], int]) -> int:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(pid)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    raw = load(pid)
    # only a real integer counts: anything else (a missing row, a stand-in
    # object) reads as epoch 0, the value every session starts with
    value = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
    with _lock:
        _cache[pid] = (now, value)
    return value


def session_is_current(pid, session_epoch, load: Callable[[int], int]) -> bool:
    """False only when the profile's epoch has moved past the session's. a
    read failure keeps the session: a db hiccup must not sign everyone out."""
    try:
        return int(session_epoch or 0) >= _epoch(int(pid), load)
    except Exception:  # noqa: BLE001
        return True


def forget(pid) -> None:
    with _lock:
        _cache.pop(int(pid), None)


def _reset_for_tests() -> None:
    with _lock:
        _cache.clear()


__all__ = ["session_is_current", "forget"]
