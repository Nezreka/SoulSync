"""Artist / album / track blocklist (the "proper" blacklist).

Distinct from ``download_blacklist`` (which skips one bad source file from one
Soulseek peer — untouched here). This blocklist bans an ARTIST, ALBUM, or
TRACK from being acquired, keyed by metadata-source IDs (Spotify / iTunes /
Deezer / MusicBrainz) so a ban survives a source switch.

Phase 1 enforces at the single ``add_to_wishlist`` chokepoint: every
auto-acquisition path (watchlist, discography backfill, repair, manual
wishlist add) funnels through it, so one guard covers them all.

- ``matching`` — the pure decision core (no DB, no I/O): build an index from
  blocklist rows, ask whether a candidate is blocked, with artist→album→track
  cascade.
"""

from core.blocklist.matching import (
    ENTITY_ALBUM,
    ENTITY_ARTIST,
    ENTITY_TRACK,
    ENTITY_TYPES,
    SOURCE_ID_FIELDS,
    BlocklistIndex,
    build_index,
    candidate_block_reason,
)

__all__ = [
    "ENTITY_ARTIST",
    "ENTITY_ALBUM",
    "ENTITY_TRACK",
    "ENTITY_TYPES",
    "SOURCE_ID_FIELDS",
    "BlocklistIndex",
    "build_index",
    "candidate_block_reason",
]
