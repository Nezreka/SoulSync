"""which oauth flows somebody allowed to start is still waiting on.

an oauth callback can land with no session cookie (the provider redirects to
127.0.0.1 or another host name than the one the browser uses), so the
callback can't always ask who is calling. it asks this instead: a flow for
this account was started, by somebody allowed to start it, a few minutes ago.
a start is used up by its callback, so a code made up later binds nothing.

keys: "spotify:global", "spotify:profile:<id>", "deezer:global".
"""

from __future__ import annotations

import threading
import time

TTL_SECONDS = 15 * 60

_lock = threading.Lock()
_pending: dict = {}


def mark(key: str, now: float | None = None) -> None:
    """a flow for this key was just started by somebody allowed to."""
    now = time.time() if now is None else now
    with _lock:
        _pending[key] = now + TTL_SECONDS
        for k in [k for k, exp in _pending.items() if exp < now]:
            _pending.pop(k, None)


def consume(key: str, now: float | None = None) -> bool:
    """True once for a live start of this key, then it's gone."""
    now = time.time() if now is None else now
    with _lock:
        exp = _pending.pop(key, None)
    return exp is not None and exp >= now


def clear() -> None:
    with _lock:
        _pending.clear()


__all__ = ["mark", "consume", "clear", "TTL_SECONDS"]
