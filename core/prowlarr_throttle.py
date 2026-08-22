"""Process-wide Prowlarr search throttle — ONE budget for music and video.

Prowlarr forwards every search straight to your indexers, and indexers are the
thing that gets upset: most trackers allow a query every few seconds, and usenet
indexers usually have a daily API hit cap. Prowlarr itself does not shield them
from us.

Everything else in this app that talks to a third party is paced. The thirteen
metadata services each hold a MIN_API_INTERVAL, and slskd search creation goes
through core.slskd_throttle. Prowlarr had nothing at all, on either side, so the
video wishlist drain could run three items at once, each fanning out two or three
search STRATEGIES concurrently, and start the next item the moment one finished.
That is a search landing on every configured indexer every couple of seconds for
as long as the wishlist is long (Boulder, Aug 2026: "it will search one item on
torrent, less than 5 seconds later is doing a different search").

Same reservation model as the slskd throttle, and for the same reason: a caller
atomically reserves the next allowed time under the lock and then sleeps until
it, so two threads arriving together get two DIFFERENT slots instead of both
computing "no wait" and firing at once. A plain "sleep if the last call was
recent" check is wrong under concurrency, which is exactly the shape this
subsystem runs in.

The budget is shared by the music download plugins and the video acquisition
paths on purpose. It is one Prowlarr instance in front of one set of indexers,
and an indexer cannot tell which half of the app made the request.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

# Defaults, both overridable from settings (prowlarr.*). Chosen to be clearly
# gentler than the unbounded behaviour they replace without making a wishlist
# drain take all night: a search every 2s sustained, and a ceiling so a long
# burst cannot average faster than one every 3s.
DEFAULT_MIN_GAP_SECONDS = 2.0
DEFAULT_MAX_PER_WINDOW = 20
WINDOW_SECONDS = 60.0

_LOCK = threading.Lock()
_TIMES: list = []            # reserved search times (monotonic), pruned to the window
_COOLDOWN_UNTIL = [0.0]


def _settings() -> tuple:
    """(min_gap, max_per_window) from config, falling back to the defaults.

    Read per reservation rather than cached: the knobs live in Settings and a
    user turning them down because their indexer is complaining should not have
    to restart the app to be listened to.
    """
    try:
        from core.settings import config_manager
        gap = config_manager.get('prowlarr.search_min_gap_seconds', DEFAULT_MIN_GAP_SECONDS)
        cap = config_manager.get('prowlarr.max_searches_per_minute', DEFAULT_MAX_PER_WINDOW)
        gap = float(gap) if gap is not None else DEFAULT_MIN_GAP_SECONDS
        cap = int(cap) if cap is not None else DEFAULT_MAX_PER_WINDOW
    except Exception:  # noqa: BLE001 - config unreadable must never block a search
        return DEFAULT_MIN_GAP_SECONDS, DEFAULT_MAX_PER_WINDOW
    # 0 disables that half of the budget; negatives are meaningless.
    return max(0.0, gap), max(0, cap)


def reserve_search_slot(max_wait_seconds: Optional[float] = None) -> Optional[float]:
    """Reserve the next allowed search time (``time.monotonic()`` seconds).

    The caller sleeps until the returned time before issuing its request; see
    :func:`wait_for_slot` which does both.

    ``max_wait_seconds`` is for interactive callers, a person sitting on a
    manual search waiting for an HTTP response. When the next free slot is
    further away than that, nothing is reserved and ``None`` comes back, so the
    caller can say "busy, try again" instead of holding a request worker while a
    background drain empties the window. Background callers pass None and wait.
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
            at = max(at, _TIMES[0] + WINDOW_SECONDS)        # window full → wait it out
        at = max(at, _COOLDOWN_UNTIL[0])                    # honour a 429 cooldown
        if max_wait_seconds is not None and at - now > max_wait_seconds:
            return None                                     # don't consume a slot
        _TIMES.append(at)
        return at


def wait_for_slot(max_wait_seconds: Optional[float] = None) -> bool:
    """Reserve and then sleep until the slot. False = refused (interactive only).

    The sleep is OUTSIDE the lock, which is the whole point of reserving a time
    rather than holding the lock while waiting: twenty queued searches serialise
    on their reserved times, not on the mutex.
    """
    at = reserve_search_slot(max_wait_seconds=max_wait_seconds)
    if at is None:
        return False
    delay = at - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return True


def note_rate_limited(retry_after: Any = None) -> None:
    """An indexer or Prowlarr pushed back — every caller backs off together.

    Bounded at both ends: a missing or absurd Retry-After should not park every
    search for an hour, and anything under 5s is not a real backoff.
    """
    try:
        secs = float(retry_after) if retry_after else 30.0
    except (TypeError, ValueError):
        secs = 30.0
    with _LOCK:
        _COOLDOWN_UNTIL[0] = max(_COOLDOWN_UNTIL[0], time.monotonic() + max(5.0, min(secs, 300.0)))


def status() -> Dict[str, Any]:
    """Current budget usage, for the settings page and debug info."""
    min_gap, max_per_window = _settings()
    with _LOCK:
        now = time.monotonic()
        used = sum(1 for t in _TIMES if t > now - WINDOW_SECONDS)
        cooling = max(0.0, _COOLDOWN_UNTIL[0] - now)
    return {
        'searches_in_window': used,
        'max_searches_per_window': max_per_window,
        'window_seconds': WINDOW_SECONDS,
        'searches_remaining': max(0, max_per_window - used) if max_per_window else None,
        'min_gap_seconds': min_gap,
        'cooldown_remaining': round(cooling, 1),
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _TIMES.clear()
        _COOLDOWN_UNTIL[0] = 0.0
