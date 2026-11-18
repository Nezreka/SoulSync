"""Which tracks on an album are its popular ones (#2144).

Plexamp marks an album's hits with a little flame. We do the same from the
Last.fm playcount the Last.fm worker already keeps on every track
(``lib2_tracks.enrichment -> lastfm.playcount``), so nothing is fetched to draw
it.

Popular is relative to the album, not a fixed number: a track counts when it
was played at least twice as much as the album's middle track, and only the
top few get the mark. An album where Last.fm knows too few of the tracks, or
where every track sits close together, marks nothing.
"""

from __future__ import annotations

import json
import math
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("library2.track_popularity")

# need this many tracks with a playcount before the album says anything.
MIN_SCORED_TRACKS = 4
# a hit is played at least this many times more than the album's middle track.
ABOVE_MEDIAN = 2.0
# never more than this many marks, and about a third of a short album at most.
MAX_POPULAR = 3
# below this many plays the counts are noise, not popularity.
MIN_PLAYS = 1000


def lastfm_playcount(enrichment: Any) -> Optional[int]:
    """The Last.fm playcount out of a track's enrichment json, or None."""
    if not enrichment:
        return None
    try:
        data = json.loads(enrichment) if isinstance(enrichment, str) else enrichment
        value = (data.get("lastfm") or {}).get("playcount")
        count = int(value) if value not in (None, "") else None
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug("unreadable track enrichment: %s", e)
        return None
    return count if count and count > 0 else None


def popular_track_ids(playcounts: Mapping[int, Optional[int]]) -> Set[int]:
    """Track ids that are this album's popular ones.

    ``playcounts`` maps every track id on the album to its playcount (None when
    Last.fm has nothing), so missing data counts against the album.
    """
    total = len(playcounts)
    scored = {tid: int(n) for tid, n in playcounts.items() if n and n > 0}
    # too little data to rank: a single known track would always "win".
    if len(scored) < MIN_SCORED_TRACKS or len(scored) * 2 < total:
        return set()
    middle = median(scored.values())
    bar = max(middle * ABOVE_MEDIAN, MIN_PLAYS)
    hits = sorted(
        (tid for tid, n in scored.items() if n >= bar),
        key=lambda tid: scored[tid],
        reverse=True,
    )
    limit = min(MAX_POPULAR, math.ceil(len(scored) / 3))
    return set(hits[:limit])


def popular_ids_for_rows(rows: Iterable[Any]) -> Set[int]:
    """``popular_track_ids`` over ``lib2_tracks`` rows already in hand."""
    counts: Dict[int, Optional[int]] = {}
    for row in rows:
        keys = row.keys()
        if "id" not in keys or row["id"] is None:
            continue
        counts[int(row["id"])] = lastfm_playcount(
            row["enrichment"] if "enrichment" in keys else None)
    return popular_track_ids(counts)


__all__ = [
    "lastfm_playcount",
    "popular_ids_for_rows",
    "popular_track_ids",
]
