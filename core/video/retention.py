"""Pure retention math for the YouTube channel auto-clean.

A channel's retention setting is a small string the cog modal stores:
  ``all``        — keep everything (default; nothing is ever deleted)
  ``count_<n>``  — keep the newest N episodes by upload date
  ``days_<n>``   — keep episodes uploaded within the last N days

Episodes are aged by their UPLOAD date (``published_at``), falling back to the date in the
filename (the youtube template embeds it) so downloads from before the column existed still
work. An episode with no derivable upload date is NEVER pruned (safe). All file I/O lives in
the handler; this module just decides which episodes fall outside the keep window.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_retention(value: Any) -> Optional[Tuple[str, int]]:
    """``'count_30'`` → ``('count', 30)``; ``'all'`` / blank / junk → None (keep everything)."""
    if not value or value == "all":
        return None
    try:
        mode, raw = str(value).split("_", 1)
        n = int(raw)
    except (ValueError, AttributeError):
        return None
    return (mode, n) if (mode in ("count", "days") and n > 0) else None


def episode_date(ep: Dict[str, Any]) -> str:
    """The episode's upload date (``YYYY-MM-DD``): ``published_at`` if stored, else parsed
    from the filename. ``''`` when neither yields one."""
    p = str(ep.get("published_at") or "")[:10]
    if len(p) == 10 and _DATE_RE.fullmatch(p):
        return p
    m = _DATE_RE.search(str(ep.get("filename") or ""))
    return m.group(1) if m else ""


def episodes_to_prune(episodes: List[Dict[str, Any]], retention: Any, *, today: str) -> List[Dict[str, Any]]:
    """The episodes to DELETE under ``retention`` (newest upload kept first). Pure — episodes
    with no derivable upload date are kept. ``today`` is an ISO date for the days-based cutoff."""
    parsed = parse_retention(retention)
    if not parsed:
        return []
    mode, n = parsed
    dated = [(episode_date(e), e) for e in episodes]
    dated = sorted([(d, e) for d, e in dated if d], key=lambda t: t[0], reverse=True)
    if mode == "count":
        return [e for _, e in dated[n:]]            # everything beyond the newest n
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=n)).isoformat()
    except (ValueError, TypeError):
        return []
    return [e for d, e in dated if d < cutoff]       # uploaded before the cutoff
