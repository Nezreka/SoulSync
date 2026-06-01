"""Radio / auto-play recommendation logic.

Pure, DB-agnostic helpers that decide *what* radio should play. The SQL
execution stays in ``database.music_database.get_radio_tracks``; this package
owns the decisions (tag parsing, tier caps, dedup/collection, LIKE-condition
building) so they're unit-testable without a live DB — the seam Phase 2's
smarter ranking will plug into.
"""

from core.radio.selection import (
    RadioCollector,
    build_like_conditions,
    merge_tags,
    parse_tags,
    rank_candidates,
    same_artist_cap,
    score_candidate,
)

__all__ = [
    "RadioCollector",
    "build_like_conditions",
    "merge_tags",
    "parse_tags",
    "rank_candidates",
    "same_artist_cap",
    "score_candidate",
]
