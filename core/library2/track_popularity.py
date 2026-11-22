"""Which tracks on an album are its popular ones (#2144).

Plexamp marks an album's hits with a little flame. We do the same from the
Last.fm playcount the Last.fm worker already keeps on every track
(``lib2_tracks.enrichment -> lastfm.playcount``), so nothing is fetched to draw
it.

When Last.fm knows too little about an album (no key, or a key that needs
Pro since December 2026), ListenBrainz listen counts take over, kept under
``enrichment -> listenbrainz.listen_count`` by the free-source fallback
(core.enrichment.lastfm_fallback). The two are never mixed on one album: an
album is ranked on Last.fm's numbers or on ListenBrainz's.

Popular is relative to the album, not a fixed number: a track counts when it
was played at least twice as much as the album's middle track, and only the
top few get the mark. An album where Last.fm knows too few of the tracks, or
where every track sits close together, marks nothing.
"""

from __future__ import annotations

import json
import math
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple

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
# listenbrainz has far fewer listeners than last.fm, so its floor is lower
LISTENBRAINZ_MIN_PLAYS = 50


def _count(enrichment: Any, service: str, key: str) -> Optional[int]:
    if not enrichment:
        return None
    try:
        data = json.loads(enrichment) if isinstance(enrichment, str) else enrichment
        value = (data.get(service) or {}).get(key)
        count = int(value) if value not in (None, "") else None
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug("unreadable track enrichment: %s", e)
        return None
    return count if count and count > 0 else None


def lastfm_playcount(enrichment: Any) -> Optional[int]:
    """The Last.fm playcount out of a track's enrichment json, or None."""
    return _count(enrichment, "lastfm", "playcount")


def listenbrainz_listen_count(enrichment: Any) -> Optional[int]:
    """The ListenBrainz listen count out of a track's enrichment json, or None."""
    return _count(enrichment, "listenbrainz", "listen_count")


def enough_data(playcounts: Mapping[int, Optional[int]]) -> bool:
    """whether a source knows enough of the album's tracks to rank them"""
    scored = sum(1 for n in playcounts.values() if n and n > 0)
    return scored >= MIN_SCORED_TRACKS and scored * 2 >= len(playcounts)


def popular_track_ids(playcounts: Mapping[int, Optional[int]],
                      min_plays: int = MIN_PLAYS) -> Set[int]:
    """Track ids that are this album's popular ones.

    ``playcounts`` maps every track id on the album to its playcount (None when
    the source has nothing), so missing data counts against the album.
    """
    scored = {tid: int(n) for tid, n in playcounts.items() if n and n > 0}
    # too little data to rank: a single known track would always "win".
    if not enough_data(playcounts):
        return set()
    middle = median(scored.values())
    bar = max(middle * ABOVE_MEDIAN, min_plays)
    hits = sorted(
        (tid for tid, n in scored.items() if n >= bar),
        key=lambda tid: scored[tid],
        reverse=True,
    )
    limit = min(MAX_POPULAR, math.ceil(len(scored) / 3))
    return set(hits[:limit])


def popular_for_rows(rows: Iterable[Any]) -> Tuple[Set[int], Optional[str]]:
    """(popular track ids, whose numbers ranked them) over ``lib2_tracks``
    rows already in hand. Last.fm when it knows enough of the album, else
    ListenBrainz; (set(), None) when neither does."""
    lastfm: Dict[int, Optional[int]] = {}
    listenbrainz: Dict[int, Optional[int]] = {}
    for row in rows:
        keys = row.keys()
        if "id" not in keys or row["id"] is None:
            continue
        enrichment = row["enrichment"] if "enrichment" in keys else None
        lastfm[int(row["id"])] = lastfm_playcount(enrichment)
        listenbrainz[int(row["id"])] = listenbrainz_listen_count(enrichment)
    if enough_data(lastfm):
        return popular_track_ids(lastfm), "lastfm"
    if enough_data(listenbrainz):
        return popular_track_ids(listenbrainz, LISTENBRAINZ_MIN_PLAYS), "listenbrainz"
    return set(), None


def popular_ids_for_rows(rows: Iterable[Any]) -> Set[int]:
    """``popular_track_ids`` over ``lib2_tracks`` rows already in hand."""
    return popular_for_rows(rows)[0]


__all__ = [
    "enough_data",
    "lastfm_playcount",
    "listenbrainz_listen_count",
    "popular_for_rows",
    "popular_ids_for_rows",
    "popular_track_ids",
]
