"""Progressive retry backoff for failing wishlist tracks (javiavid).

Every scheduled wishlist cycle used to retry EVERY failing track — a track
that's been unavailable for months got a full search burned on it every hour,
forever, out of the shared slskd search budget. Once the per-track attempt
counter is real (retry_count + last_attempted, stamped after every failed
cycle), each track earns a cooldown that grows with its failure count:

    attempts 0-1  →  no cooldown (every cycle, as before)
    attempts 2    →  4 hours
    attempts 3    →  24 hours
    attempts 4+   →  7 days

Tracks never auto-abandon — a 7-day cadence keeps watching indefinitely, and
the Failing filter + manual search stay the escalation path. The MANUAL
"Process Wishlist Now" button bypasses backoff entirely (the click is the
override, like Sonarr's manual search); only scheduled cycles apply it.

Timestamps: wishlist_tracks.last_attempted is SQLite CURRENT_TIMESTAMP — UTC,
'YYYY-MM-DD HH:MM:SS'. Comparisons here are UTC-to-UTC. Everything fails OPEN
(unparseable/missing timestamp → due) — backoff must never strand a track.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

_HOUR = 3600
_LADDER = {2: 4 * _HOUR, 3: 24 * _HOUR}
_MAX_COOLDOWN = 7 * 24 * _HOUR


def cooldown_seconds(retry_count: Any) -> int:
    """Cooldown a track has earned from its failure count."""
    try:
        n = int(retry_count or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 1:
        return 0
    return _LADDER.get(n, _MAX_COOLDOWN)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def is_due(track: Dict[str, Any], now_utc: datetime) -> bool:
    """Is this track eligible for the current scheduled cycle? Fail-open."""
    cd = cooldown_seconds(track.get('retry_count'))
    if cd <= 0:
        return True
    last = _parse_ts(track.get('last_attempted'))
    if last is None:
        return True
    return (now_utc - last).total_seconds() >= cd


def split_due_for_retry(
    tracks: Iterable[Dict[str, Any]], now_utc: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(due, cooling) — cooling tracks sit this scheduled cycle out."""
    due: List[Dict[str, Any]] = []
    cooling: List[Dict[str, Any]] = []
    for t in tracks:
        (due if is_due(t, now_utc) else cooling).append(t)
    return due, cooling
