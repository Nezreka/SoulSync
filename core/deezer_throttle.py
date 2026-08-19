"""Process-wide Deezer API throttle — ONE budget for every caller.

Deezer publishes no rate limit. Their developer FAQ says only "there is no
limitation on data in the API, but there is a query quota" and links to a page
carrying no number; the API sends no ``X-RateLimit-*`` or ``Retry-After``
headers, so there is no signal telling us how close we are. The widely repeated
community figure is **50 requests per 5 seconds**, and it is the only figure
there is. We budget 40 of that 50 and keep the rest as margin for being wrong.

There is no way to buy more: "There is no paid API", and whitelisting is "not
possible, unless there is a commercial agreement".

Why one budget rather than a number per caller: ten modules talk to Deezer —
the background enrichment worker, the watchlist scanner, the soulid worker,
artist detail, liked-match, metadata service, playlist loading, search. They run
at the same time against one quota, so a per-caller cap is arithmetic that never
adds up. Before this, ``@rate_limited`` in deezer_client enforced 1 req/s on
nine methods while the two playlist album loops called ``session.get`` directly
with their own ``time.sleep`` and were counted by nothing at all.

Reservation model lifted from ``core.slskd_throttle``, which solves the same
problem for slskd: a caller atomically reserves the next allowed time under the
lock, then sleeps until it — two threads arriving together get two *different*
slots instead of both computing "no wait". Deliberately the same shape, because
two throttles that drift apart is how the per-caller mess started.

Deezer signals an exceeded quota in the response BODY, not the status code:
``{"error": {"type": "Exception", "code": 4, "message": "Quota limit exceeded"}}``
with HTTP 200. ``note_quota_exceeded`` is how a caller reports that.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

# 40 of the reported 50 per 5s — 8/s sustained, 20% held back because the 50 is
# community folklore rather than a documented figure.
MAX_PER_WINDOW = 40
WINDOW_SECONDS = 5.0

# Deezer's error code for an exceeded quota, returned with HTTP 200 in the body.
QUOTA_ERROR_CODE = 4

# Pace evenly instead of firing MAX_PER_WINDOW at once and idling. A burst is
# legal on paper — an empty window is free — but the calls land a little later
# than their slot (sleep overshoot, thread scheduling), so two adjacent bursts
# smear into each other and a real 5s window sees more than the cap. Measured
# 41-46 against a cap of 40 that way. At one call per WINDOW/MAX the budget
# holds under jitter, and 8/s sustained is the point of the exercise.
MIN_GAP_SECONDS = WINDOW_SECONDS / MAX_PER_WINDOW    # 0.125s

_LOCK = threading.Lock()
_TIMES: list = []          # reserved call times (monotonic), pruned to the window
_COOLDOWN_UNTIL = [0.0]


def reserve_slot(max_wait_seconds: float | None = None) -> float | None:
    """Reserve the next allowed call time (``time.monotonic()`` seconds).

    The caller sleeps until it before issuing the request. ``max_wait_seconds``
    is for interactive callers: when the next slot is further out than that,
    nothing is reserved and ``None`` comes back, so a request worker isn't held
    for minutes while a background scan drains the window.
    """
    with _LOCK:
        now = time.monotonic()
        while _TIMES and _TIMES[0] <= now - WINDOW_SECONDS:
            _TIMES.pop(0)
        at = now
        if _TIMES:
            at = max(at, _TIMES[-1] + MIN_GAP_SECONDS)     # space from the last reservation
        if len(_TIMES) >= MAX_PER_WINDOW:
            # Space from the reservation that has to age out to make room for
            # THIS one — counting back MAX_PER_WINDOW from the end, not from
            # _TIMES[0]. Off _TIMES[0] every queued caller computes the same
            # deadline and they all fire together: measured 15.8 req/s against a
            # target of 8, and 41 calls inside a 5s window. slskd's copy hides
            # this behind its min_gap_seconds, which this one has no use for.
            at = max(at, _TIMES[len(_TIMES) - MAX_PER_WINDOW] + WINDOW_SECONDS)
        at = max(at, _COOLDOWN_UNTIL[0])                   # honour a quota cooldown
        if max_wait_seconds is not None and at - now > max_wait_seconds:
            return None                                    # don't consume a slot
        _TIMES.append(at)
        return at


def wait_for_slot(max_wait_seconds: float | None = None) -> bool:
    """Reserve a slot and sleep until it. False when nothing was reserved.

    The convenience most callers want — reserving without sleeping is only
    useful to async code that awaits the deadline itself.
    """
    at = reserve_slot(max_wait_seconds=max_wait_seconds)
    if at is None:
        return False
    delay = at - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return True


def note_quota_exceeded(retry_after: Any = None) -> None:
    """Deezer reported the quota exceeded — every caller backs off together.

    Without this, one caller walks into the wall and the other nine follow it in
    one at a time.
    """
    try:
        secs = float(retry_after) if retry_after else 10.0
    except (TypeError, ValueError):
        secs = 10.0
    with _LOCK:
        _COOLDOWN_UNTIL[0] = time.monotonic() + max(5.0, min(secs, 120.0))


def is_quota_error(payload: Any) -> bool:
    """True when a Deezer JSON body carries the quota error.

    Deezer answers HTTP 200 and puts the failure in the body, so a caller that
    only checks ``resp.ok`` sails straight past it.
    """
    if not isinstance(payload, dict):
        return False
    err = payload.get('error')
    if not isinstance(err, dict):
        return False
    try:
        return int(err.get('code')) == QUOTA_ERROR_CODE
    except (TypeError, ValueError):
        return False


def status() -> Dict[str, Any]:
    """Current budget usage, for the call tracker and debug export."""
    with _LOCK:
        now = time.monotonic()
        used = sum(1 for t in _TIMES if t > now - WINDOW_SECONDS)
        cooling = max(0.0, _COOLDOWN_UNTIL[0] - now)
    return {
        'calls_in_window': used,
        'max_calls_per_window': MAX_PER_WINDOW,
        'window_seconds': WINDOW_SECONDS,
        'calls_remaining': max(0, MAX_PER_WINDOW - used),
        'cooldown_remaining': round(cooling, 2),
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _TIMES.clear()
        _COOLDOWN_UNTIL[0] = 0.0
