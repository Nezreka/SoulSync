"""Process-wide LRCLib request throttle — ONE budget for every caller.

LRCLib is a free community service with approximate allowances (~45
requests/min for generic clients, ~90/min with an identifying User-Agent).
SoulSync had no pacing at all: the Lyrics Filler scan, the repair worker's
Fix All, imports, retag and the player endpoint each called LRCLib as fast
as they could, so a big library produced bursts of hundreds of
requests/min. LRCLib answered 429/503, and the lyrics client then reported
those tracks as "lyrics no longer available" (#1879).

Same reservation model as core/prowlarr_throttle and core/deezer_throttle,
and for the same reason: a caller atomically reserves the next allowed
time under the lock and then sleeps until it, so two threads arriving
together get two DIFFERENT slots instead of both computing "no wait" and
firing at once. A plain "sleep if the last call was recent" check is wrong
under concurrency, which is exactly the shape the repair worker runs in.

The budget lives at the LyricsClient layer, so every caller that goes
through the shared client (scan, fixer, import, retag, player) is covered
by the same budget.
"""

from __future__ import annotations

import bisect
import threading
import time
from typing import Any, Dict, Optional

# Defaults, both overridable from settings (lrclib.*). Chosen to sit under
# LRCLib's documented identifying-User-Agent allowance (~90/min) with
# headroom: a request every 0.75s sustained, and a ceiling of 80 requests
# in any 60s window so a burst cannot average faster than that.
DEFAULT_MIN_GAP_SECONDS = 0.75
DEFAULT_MAX_PER_WINDOW = 80
WINDOW_SECONDS = 60.0

_LOCK = threading.Lock()
_TIMES: list = []            # reserved request times (monotonic), pruned to the window
_COOLDOWN_UNTIL = [0.0]


def _settings() -> tuple:
    """(min_gap, max_per_window) from config, falling back to the defaults.

    Read per reservation rather than cached: the knobs live in Settings and
    a user turning them down because LRCLib is complaining should not have
    to restart the app to be listened to.
    """
    try:
        from core.settings import config_manager
        gap = config_manager.get('lrclib.request_min_gap_seconds', DEFAULT_MIN_GAP_SECONDS)
        cap = config_manager.get('lrclib.max_requests_per_minute', DEFAULT_MAX_PER_WINDOW)
        gap = float(gap) if gap is not None else DEFAULT_MIN_GAP_SECONDS
        cap = int(cap) if cap is not None else DEFAULT_MAX_PER_WINDOW
    except Exception:  # noqa: BLE001 - config unreadable must never block a lookup
        return DEFAULT_MIN_GAP_SECONDS, DEFAULT_MAX_PER_WINDOW
    # 0 disables that half of the budget; negatives are meaningless.
    return max(0.0, gap), max(0, cap)


def reserve_request_slot(max_wait_seconds: Optional[float] = None) -> Optional[float]:
    """Reserve the next allowed request time (``time.monotonic()`` seconds).

    The caller sleeps until the returned time before issuing its request;
    see :func:`wait_for_slot` which does both.

    ``max_wait_seconds`` is for interactive callers. When the next free
    slot is further away than that, nothing is reserved and ``None`` comes
    back, so the caller can proceed knowing it was refused rather than
    holding a request worker. Background callers pass None and wait.
    """
    min_gap, max_per_window = _settings()
    with _LOCK:
        now = time.monotonic()
        while _TIMES and _TIMES[0] <= now - WINDOW_SECONDS:
            _TIMES.pop(0)
        at = now
        if _TIMES and min_gap > 0:
            at = max(at, _TIMES[-1] + min_gap)              # space from the last reservation
        if max_per_window and len(_TIMES) >= max_per_window:
            # Window full → wait until the max_per_window-th most recent
            # reservation leaves the window. (Using _TIMES[0] here would
            # collapse every reservation past the cap onto the same
            # instant whenever the list holds more than the cap — e.g.
            # with min_gap = 0 — and fire them as a burst.)
            at = max(at, _TIMES[-max_per_window] + WINDOW_SECONDS)
        at = max(at, _COOLDOWN_UNTIL[0])                    # honour a 429/5xx cooldown
        if max_wait_seconds is not None and at - now > max_wait_seconds:
            return None                                     # don't consume a slot
        # insort, not append: with min_gap = 0 a reservation pushed out
        # by the window/cooldown can be followed by one at `now`, which
        # would leave the list unsorted and break front-pruning.
        bisect.insort(_TIMES, at)
        return at


def wait_for_slot(max_wait_seconds: Optional[float] = None) -> bool:
    """Reserve and then sleep until the slot. False = refused (interactive only).

    The sleep is OUTSIDE the lock, which is the whole point of reserving a
    time rather than holding the lock while waiting: queued lookups
    serialise on their reserved times, not on the mutex.
    """
    at = reserve_request_slot(max_wait_seconds=max_wait_seconds)
    if at is None:
        return False
    delay = at - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return True


def note_rate_limited(retry_after: Any = None) -> None:
    """LRCLib pushed back (429/5xx) — every caller backs off together.

    Bounded at both ends: a missing or absurd Retry-After should not park
    every lookup for an hour, and anything under 5s is not a real backoff.
    """
    try:
        # None (no header) → default; an explicit 0 is a real value and
        # must not be read as "missing" — it still earns the 5s floor.
        secs = float(retry_after) if retry_after is not None else 30.0
    except (TypeError, ValueError):
        secs = 30.0
    with _LOCK:
        _COOLDOWN_UNTIL[0] = max(_COOLDOWN_UNTIL[0], time.monotonic() + max(5.0, min(secs, 300.0)))


def status() -> Dict[str, Any]:
    """Current budget usage, for debug info."""
    min_gap, max_per_window = _settings()
    with _LOCK:
        now = time.monotonic()
        used = sum(1 for t in _TIMES if t > now - WINDOW_SECONDS)
        cooling = max(0.0, _COOLDOWN_UNTIL[0] - now)
    return {
        'requests_in_window': used,
        'max_requests_per_window': max_per_window,
        'window_seconds': WINDOW_SECONDS,
        'requests_remaining': max(0, max_per_window - used) if max_per_window else None,
        'min_gap_seconds': min_gap,
        'cooldown_remaining': round(cooling, 1),
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _TIMES.clear()
        _COOLDOWN_UNTIL[0] = 0.0
