"""where each track of a mirrored playlist stands (#1721).

a playlist of 157 synced as 103 and nothing said what happened to the other
54. every answer was already stored on the track rows, nobody showed it:

- in_library: the sync matcher found the file (extra_data.in_library)
- wishlisted: it's queued to download
- needs_match: identification only got a wing it guess, or nothing at all,
  so nothing will download it until someone picks the right song
- missing: identified, synced, not in the library and not queued
- unchecked: never synced, so nobody has looked yet

one status per track, first answer wins in that order. a wing it guess the
library already has is in_library, not needs_match: it's on the playlist.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Set

from core.discovery.wing_it import is_stub_id

IN_LIBRARY = "in_library"
WISHLISTED = "wishlisted"
NEEDS_MATCH = "needs_match"
MISSING = "missing"
UNCHECKED = "unchecked"

STATUSES = (IN_LIBRARY, WISHLISTED, NEEDS_MATCH, MISSING, UNCHECKED)


def parse_extra(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _matched_id(extra: Dict[str, Any]) -> str:
    md = extra.get("matched_data")
    return str((md or {}).get("id") or "").strip() if isinstance(md, dict) else ""


def needs_match(extra: Dict[str, Any]) -> bool:
    """identification left this track without a real song behind it."""
    if extra.get("manual_match"):
        return False
    if extra.get("discovered"):
        # the flag outlives a later real match, the stub id doesn't
        return bool(extra.get("wing_it_fallback")) and is_stub_id(_matched_id(extra))
    # tried and found nothing, or a user removed the match on purpose
    return bool(extra.get("discovery_attempted") or extra.get("unmatched_by_user"))


def wishlist_ids(track: Dict[str, Any], extra: Dict[str, Any] = None) -> List[str]:
    """the ids a wishlist row for this track could be filed under. sync files
    it under the matched id (often deezer's), older paths under the source id."""
    extra = parse_extra(track.get("extra_data")) if extra is None else extra
    ids = []
    for value in (_matched_id(extra), str(track.get("source_track_id") or "").strip()):
        if value and not is_stub_id(value) and value not in ids:
            ids.append(value)
    return ids


def track_status(track: Dict[str, Any], wishlisted: Set[str]) -> str:
    """one status for one mirrored track row. ``wishlisted`` is the set of ids
    the wishlist holds, out of everything wishlist_ids asked about."""
    extra = parse_extra(track.get("extra_data"))
    if extra.get("in_library") is True:
        return IN_LIBRARY
    if any(i in wishlisted for i in wishlist_ids(track, extra)):
        return WISHLISTED
    if needs_match(extra):
        return NEEDS_MATCH
    if extra.get("library_checked_at"):
        return MISSING
    return UNCHECKED


def status_counts(statuses: Iterable[str]) -> Dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for s in statuses:
        if s in counts:
            counts[s] += 1
    return counts


__all__ = [
    "IN_LIBRARY",
    "MISSING",
    "NEEDS_MATCH",
    "STATUSES",
    "UNCHECKED",
    "WISHLISTED",
    "needs_match",
    "parse_extra",
    "status_counts",
    "track_status",
    "wishlist_ids",
]
