"""Amazon enrichment outage detection + back-off — pure, importable, testable.

The Amazon worker enriches via a public T2Tunes proxy instance. When that
instance is down (HTTP 5xx, "Amazon Music API is not initialized", or an
unreachable host), the worker must NOT treat every album as an individual
failure: doing so floods the logs with an error per item, churns network + DB
continuously, and permanently marks the whole library ``error`` (which the
retry tiers never re-attempt) for what is really a transient outage.

Instead it recognizes "the whole source is down", leaves the item untouched so
it's retried once the instance recovers, and backs off hard. These two pure
helpers carry that logic so it can be unit-tested without the worker, the DB,
or the network.
"""

from __future__ import annotations

import re

# HTTP statuses that mean "the source/proxy is unhealthy", not "no match".
_OUTAGE_STATUS = {500, 502, 503, 504}

# Substrings (lower-cased) in an error message that indicate a source outage
# rather than a per-item miss: proxy not ready, gateway errors, the host being
# unreachable, or an error page returned instead of JSON.
_OUTAGE_PHRASES = (
    "not initialized", "not configured", "service unavailable",
    "bad gateway", "gateway time", "request failed", "response not json",
    "max retries", "connection", "timed out", "temporarily unavailable",
)

# Back-off schedule while the source is down.
_NORMAL_DELAY = 2          # seconds between items when healthy
_OUTAGE_BASE = 30          # first back-off step
_OUTAGE_CAP = 1800         # 30 minutes max


def is_source_outage(exc: Exception) -> bool:
    """True when ``exc`` indicates the Amazon source/proxy is down (transient,
    whole-source), as opposed to a normal per-item error.

    Robust to how the error is surfaced: an explicit ``status_code`` attribute,
    an ``HTTP <code>`` prefix in the message, or an outage phrase (covers
    connection failures and non-JSON error pages that carry no status code)."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _OUTAGE_STATUS:
        return True
    msg = str(exc).lower()
    m = re.search(r"http\s+(\d{3})", msg)
    if m and int(m.group(1)) in _OUTAGE_STATUS:
        return True
    return any(p in msg for p in _OUTAGE_PHRASES)


def next_poll_delay_seconds(outage_streak: int) -> int:
    """Seconds to wait before the next item. Normal cadence when healthy;
    escalating back-off (30s, 60s, 120s, … capped at 30 min) the longer the
    source has been down, so a dead instance can't flood logs/CPU/DB."""
    if outage_streak <= 0:
        return _NORMAL_DELAY
    return min(_OUTAGE_BASE * (2 ** min(outage_streak - 1, 6)), _OUTAGE_CAP)
