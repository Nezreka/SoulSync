"""Pure radio-selection decisions, lifted out of the DB layer.

``database.music_database.get_radio_tracks`` used to inline all of this between
``cursor.execute`` calls, so the algorithm couldn't be tested without a live DB
(which also happens to throw in the dev sandbox). These helpers carry the same
behavior as before — they're a faithful extraction, not a rewrite — but as
plain functions they're unit-testable and give Phase 2 (smart ranking) a clean
place to evolve the logic.

Nothing here touches sqlite; callers pass already-fetched rows (as dicts) and
get back decisions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def parse_tags(raw_val: Any) -> List[str]:
    """Parse a genre/mood/style field into a list of tags.

    The field may be a JSON array (canonical) or a legacy comma-separated
    string. Mirrors the inline ``_parse_tags`` the DB method used.
    """
    if not raw_val:
        return []
    try:
        parsed = json.loads(raw_val)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, ValueError, TypeError):
        return [t.strip() for t in str(raw_val).split(",") if t.strip()]


def same_artist_cap(limit: int) -> int:
    """How many same-artist tracks tier 1 may contribute.

    Capped so radio doesn't become an all-one-artist playlist: 30% of the
    limit, floored at 5 (matches the original ``max(5, limit * 3 // 10)``).
    """
    return max(5, limit * 3 // 10)


def merge_tags(*tag_groups: Iterable[str]) -> List[str]:
    """Concatenate tag lists, dedupe, preserve first-seen order.

    Mirrors ``list(dict.fromkeys(a + b))`` used for genre/mood/style merges.
    """
    merged: List[str] = []
    for group in tag_groups:
        for tag in group:
            merged.append(tag)
    return list(dict.fromkeys(merged))


def build_like_conditions(
    tags: Sequence[str], columns: Sequence[str]
) -> Tuple[str, List[str]]:
    """Build an OR-of-LIKEs SQL fragment + params for matching ``tags``
    against each of ``columns``.

    Returns ``(sql_fragment, params)`` where the fragment is
    ``"col1 LIKE ? OR col1 LIKE ? OR col2 LIKE ? ..."`` (one LIKE per
    column per tag) and params are the ``%tag%`` wildcards in matching
    order. Returns ``("", [])`` when there are no tags or no columns, so
    callers can skip the tier cleanly.

    This reproduces the original per-tier condition building, which paired
    every tag against album-level and artist-level columns.
    """
    if not tags or not columns:
        return "", []
    conditions: List[str] = []
    params: List[str] = []
    # Group by column (all tags for column A, then all tags for column B) to
    # match the original ordering: it emitted every ``al.<f> LIKE ?`` then
    # every ``ar.<f> LIKE ?``, with params being ``[%tag%...] * 2``.
    for col in columns:
        for tag in tags:
            conditions.append(f"{col} LIKE ?")
            params.append(f"%{tag}%")
    return " OR ".join(conditions), params


class RadioCollector:
    """Accumulates radio candidates across tiers with dedup + cap logic.

    Replaces the inline ``collected`` list + ``seen_ids`` set + ``_collect``
    closure the DB method used. Construct with the overall ``limit`` and the
    set of IDs to exclude up front (seed track + caller-supplied), then feed
    each tier's fetched rows through :meth:`collect`.
    """

    def __init__(self, limit: int, exclude_ids: Optional[Iterable[Any]] = None):
        self.limit = limit
        self._collected: List[Dict[str, Any]] = []
        # seen_ids seeds with the exclude set so excluded tracks never collect
        # AND so the placeholders/values used in WHERE ... NOT IN stay in sync.
        self._seen: set[str] = {str(e) for e in (exclude_ids or [])}

    @property
    def tracks(self) -> List[Dict[str, Any]]:
        return self._collected

    @property
    def filled(self) -> bool:
        """True once we've reached the overall limit."""
        return len(self._collected) >= self.limit

    def exclude_placeholders(self) -> str:
        """SQL ``?,?,...`` placeholder string sized to the current seen set."""
        return ",".join("?" * len(self._seen))

    def exclude_values(self) -> List[str]:
        """Param values for the placeholders above (current seen set)."""
        return list(self._seen)

    def remaining(self) -> int:
        """How many more tracks are needed to hit the limit."""
        return max(0, self.limit - len(self._collected))

    def collect(self, rows: Iterable[Dict[str, Any]], cap: Optional[int] = None) -> bool:
        """Append ``rows`` (dict-like) to the result, skipping already-seen IDs.

        ``cap`` bounds how many THIS call may add (on top of what's already
        collected); ``None`` means bounded only by the overall limit. Returns
        True once the overall limit is reached. Mirrors the original
        ``_collect`` closure exactly.
        """
        target = min(self.limit, len(self._collected) + cap) if cap else self.limit
        for row in rows:
            r = dict(row)
            rid = str(r["id"])
            if rid not in self._seen:
                self._seen.add(rid)
                self._collected.append(r)
                if len(self._collected) >= target:
                    return True
        return self.filled
