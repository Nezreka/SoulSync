"""the discover warmer's "recompute, don't read the cache" switch.

the warmer sweeps every 25 min and the shelf caches last 30. a sweep that
reads the cache like a user does finds every answer still valid, so every
other sweep refreshed nothing: the answers expired at 30 min and sat cold
until the next real compute at ~50, and whoever opened discover in between
paid 20 s+ per shelf. inside `refresh()` the caches skip the read and store
a fresh answer.

it's a thread-local, not a header, so nobody outside the process can force
a recompute. the warmer's test client runs its requests on the warmer's own
thread, so the flag reaches them and no real request ever sees it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

_state = threading.local()


def refreshing() -> bool:
    return bool(getattr(_state, 'on', False))


@contextmanager
def refresh():
    prev = refreshing()
    _state.on = True
    try:
        yield
    finally:
        _state.on = prev
