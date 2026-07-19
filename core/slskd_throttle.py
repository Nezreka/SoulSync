"""Process-wide slskd search-creation throttle — ONE budget for both sides.

slskd rate-limits search creation (429s beyond roughly 35 searches per 220s)
and it's one slskd instance serving this whole process. The music client and
the video downloader each used to enforce that cap with their own private
counter, so with both sides active the process could legally fire ~70
searches per window at a server that allows 35. This module owns the single
sliding window every search creation reserves from.

Reservation model (lifted from the video side, which is correct under
concurrency): a caller atomically reserves the next allowed creation time
under the lock, then sleeps/awaits until it — two threads that arrive
together get two *different* slots instead of both computing "no wait".

``min_gap_seconds`` is per-caller (video passes a fixed 2s burst-smoother,
music passes the ``soulseek.search_min_delay_seconds`` knob) but spaces from
the previous reservation *regardless of side* — the peer-connection burst it
smooths happens at the network level, where music and video searches are
indistinguishable.

A reported 429 (``note_rate_limited``) sets a shared cooldown so every
caller backs off together instead of the other side walking into the same
wall.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

MAX_PER_WINDOW = 35
WINDOW_SECONDS = 220.0

_LOCK = threading.Lock()
_TIMES: list = []          # reserved creation times (monotonic), pruned to the window
_COOLDOWN_UNTIL = [0.0]


def reserve_search_slot(min_gap_seconds: float = 0.0,
                        max_wait_seconds: float | None = None) -> float | None:
    """Reserve the next allowed search-creation time (``time.monotonic()``
    seconds); the caller sleeps/awaits until it before POSTing.

    ``max_wait_seconds`` is for interactive callers (a user waiting on an
    HTTP response): when the next slot is further away than that, nothing is
    reserved and ``None`` is returned — the caller reports "rate limited, try
    again shortly" instead of blocking a request worker for minutes while
    background sync drains the window."""
    with _LOCK:
        now = time.monotonic()
        while _TIMES and _TIMES[0] <= now - WINDOW_SECONDS:
            _TIMES.pop(0)
        at = now
        if _TIMES and min_gap_seconds > 0:
            at = max(at, _TIMES[-1] + min_gap_seconds)     # space from the last reservation
        if len(_TIMES) >= MAX_PER_WINDOW:
            at = max(at, _TIMES[0] + WINDOW_SECONDS)       # window full → wait it out
        at = max(at, _COOLDOWN_UNTIL[0])                   # honor a 429 cooldown
        if max_wait_seconds is not None and at - now > max_wait_seconds:
            return None                                    # don't consume a slot
        _TIMES.append(at)
        return at


def note_rate_limited(retry_after: Any = None) -> None:
    """slskd returned 429 — every caller backs off before the next search."""
    try:
        secs = float(retry_after) if retry_after else 30.0
    except (TypeError, ValueError):
        secs = 30.0
    with _LOCK:
        _COOLDOWN_UNTIL[0] = time.monotonic() + max(5.0, min(secs, 120.0))


def status() -> Dict[str, Any]:
    """Current budget usage — shape matches the old music-side
    ``get_rate_limit_status`` payload."""
    with _LOCK:
        now = time.monotonic()
        used = sum(1 for t in _TIMES if t > now - WINDOW_SECONDS)
    return {
        'searches_in_window': used,
        'max_searches_per_window': MAX_PER_WINDOW,
        'window_seconds': WINDOW_SECONDS,
        'searches_remaining': max(0, MAX_PER_WINDOW - used),
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _TIMES.clear()
        _COOLDOWN_UNTIL[0] = 0.0
