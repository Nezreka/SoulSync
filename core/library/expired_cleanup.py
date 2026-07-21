"""Pure expiry decision for the Expired Download Cleaner job.

Decides which origin-tracked downloads (watchlist / playlist, recorded by the
Download Origins provenance) are past their retention window and safe to
propose for deletion. No DB, no clock, no I/O — the job annotates each entry
with the facts (play_count, whether it's still in an active mirror) and this
module decides. Fully unit-testable.

A download is proposed for deletion ONLY when ALL hold:
- its origin's retention is set (not 'off') and it's older than that window,
- it's NOT protected (still in an actively-mirrored playlist / watched artist),
- it has been played FEWER than ``min_plays`` times (default 2 → "played more
  than once is kept"; play_count is the reliable signal, last_played is not).

Anything failing a check is kept. Deliberately conservative — this deletes the
user's files.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

# Retention option → days. 'off' (or anything unmapped) disables that origin.
RETENTION_DAYS = {
    "1w": 7, "2w": 14, "3w": 21, "4w": 28,
    "2mo": 60, "3mo": 90, "6mo": 180,
}
RETENTION_OPTIONS = ["off", "1w", "2w", "3w", "4w", "2mo", "3mo", "6mo"]


def retention_cutoff(retention: Optional[str], now: datetime) -> Optional[datetime]:
    """Datetime before which an entry of this retention is expired, or None
    when the retention is off/unknown (origin never auto-cleaned)."""
    days = RETENTION_DAYS.get((retention or "").strip().lower())
    if not days:
        return None
    return now - timedelta(days=days)


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a SQLite CURRENT_TIMESTAMP (UTC, no zone) or ISO string."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_expired(
    entry: Dict[str, Any],
    *,
    watchlist_retention: Optional[str],
    playlist_retention: Optional[str],
    min_plays: int,
    now: datetime,
) -> bool:
    """True if this origin entry should be proposed for deletion.

    ``entry`` needs: ``origin`` ('watchlist'|'playlist'), ``created_at``,
    ``play_count`` (int, may be None), ``protected`` (bool — still in an active
    mirror/watch)."""
    if entry.get("protected"):
        return False
    if (entry.get("play_count") or 0) >= max(1, int(min_plays or 1)):
        return False  # listened to enough to keep
    origin = (entry.get("origin") or "").strip().lower()
    retention = watchlist_retention if origin == "watchlist" else playlist_retention
    cutoff = retention_cutoff(retention, now)
    if cutoff is None:
        return False  # this origin's auto-clean is off
    created = _parse_ts(entry.get("created_at"))
    if created is None:
        return False  # unknown age → never delete
    return created < cutoff


def select_expired(
    entries: Iterable[Dict[str, Any]],
    *,
    watchlist_retention: Optional[str],
    playlist_retention: Optional[str],
    min_plays: int = 2,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return the subset of ``entries`` that are expired + safe to delete."""
    now = now or datetime.now(timezone.utc)
    return [
        e for e in (entries or [])
        if is_expired(e, watchlist_retention=watchlist_retention,
                      playlist_retention=playlist_retention,
                      min_plays=min_plays, now=now)
    ]
