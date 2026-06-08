"""Release-date gating (#705): keep unreleased tracks out of hot paths.

Watchlist scans intentionally pick up ANNOUNCED albums — singles drop early,
the rest of the tracklist carries a future release date. Two places must not
treat those as available:

  - the wishlist auto-processor: searching Soulseek for a track that isn't
    out yet burns a full search+timeout per track, every cycle
  - the Fresh Tape / Release Radar builder: future albums got NEGATIVE
    days_old, which INFLATED their recency score (100 - days*7) above every
    released track — prereleases weren't just slipping in, they were favored

Spotify-style dates come in three precisions: YYYY, YYYY-MM, YYYY-MM-DD.
The gate is deliberately conservative: a track is "unreleased" only when the
date is CONFIDENTLY in the future at its stated precision. Unparseable or
missing dates are treated as released (never block on bad data), and a
release dated today counts as released — release-day tracks should flow.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple


def is_future_release(release_date_str: Any, today: Optional[date] = None) -> bool:
    """True only when ``release_date_str`` is confidently in the future."""
    if not release_date_str or not isinstance(release_date_str, str):
        return False
    today = today or date.today()
    parts = release_date_str.strip().split('-')
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        return False
    if len(parts) == 1 or not parts[1]:
        return year > today.year
    try:
        month = int(parts[1])
    except ValueError:
        return year > today.year
    if not 1 <= month <= 12:
        # Garbage month — fall back to year precision, never block on it.
        return year > today.year
    if len(parts) == 2 or not parts[2]:
        return (year, month) > (today.year, today.month)
    try:
        day = int(parts[2][:2])
    except ValueError:
        return (year, month) > (today.year, today.month)
    try:
        return date(year, month, day) > today
    except ValueError:
        return (year, month) > (today.year, today.month)


def track_release_date(track: Dict[str, Any]) -> str:
    """Pull the release date off a track dict in its common shapes."""
    if not isinstance(track, dict):
        return ''
    album = track.get('album')
    if isinstance(album, dict) and album.get('release_date'):
        return str(album['release_date'])
    return str(track.get('release_date') or '')


def split_released_unreleased(
    tracks: Iterable[Dict[str, Any]],
    today: Optional[date] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition tracks into (released, unreleased) by their release date."""
    released: List[Dict[str, Any]] = []
    unreleased: List[Dict[str, Any]] = []
    for t in tracks:
        if is_future_release(track_release_date(t), today=today):
            unreleased.append(t)
        else:
            released.append(t)
    return released, unreleased
