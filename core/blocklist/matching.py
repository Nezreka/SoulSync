"""Pure blocklist matching — no DB, no I/O, fully unit-testable.

The brain of the blocklist: given the stored blocklist rows and a candidate
track being considered for the wishlist, decide whether it's blocked.

Design decisions (per Boulder):
- **ID-keyed.** Each row carries the candidate's IDs in up to four metadata
  sources. A candidate is matched against the SAME source it came in on
  (the wishlist payload carries active-source IDs), so a Deezer-numeric id
  can't collide with an iTunes-numeric id of a different entity.
- **Cascade.** Blocking an artist blocks their albums + tracks; blocking an
  album blocks its tracks. The candidate carries its own artist/album/track
  IDs, so the check walks track → album → artist and blocks on the first hit.
- **Name fallback for ARTISTS only.** A blocked artist also matches by
  case-folded name — this covers the window before the background ID-backfill
  has resolved the active source's id. Albums/tracks do NOT fall back to name
  (common titles like "Greatest Hits" would false-positive across artists);
  they rely on IDs, which backfill fills in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ENTITY_ARTIST = "artist"
ENTITY_ALBUM = "album"
ENTITY_TRACK = "track"
ENTITY_TYPES = (ENTITY_ARTIST, ENTITY_ALBUM, ENTITY_TRACK)

# Blocklist-row column → the metadata source it belongs to.
SOURCE_ID_FIELDS = {
    "spotify": "spotify_id",
    "itunes": "itunes_id",
    "deezer": "deezer_id",
    "musicbrainz": "musicbrainz_id",
}


def _norm(text: Any) -> str:
    return str(text or "").strip().casefold()


@dataclass
class _TypeIndex:
    # per-source set of blocked ids, plus a case-folded name set (artists only)
    ids: Dict[str, Set[str]] = field(default_factory=lambda: {s: set() for s in SOURCE_ID_FIELDS})
    names: Set[str] = field(default_factory=set)

    def hit(self, source: Optional[str], entity_id: Any, name: Any, use_name: bool) -> bool:
        if entity_id and source in self.ids and str(entity_id) in self.ids[source]:
            return True
        if use_name and name and _norm(name) in self.names:
            return True
        return False


@dataclass
class BlocklistIndex:
    """Membership index built once per scan from the blocklist rows."""
    artists: _TypeIndex = field(default_factory=_TypeIndex)
    albums: _TypeIndex = field(default_factory=_TypeIndex)
    tracks: _TypeIndex = field(default_factory=_TypeIndex)

    @property
    def is_empty(self) -> bool:
        for ti in (self.artists, self.albums, self.tracks):
            if ti.names or any(ti.ids.values()):
                return False
        return True


def build_index(rows: Iterable[Dict[str, Any]]) -> BlocklistIndex:
    """Build a BlocklistIndex from blocklist DB rows.

    Each row needs ``entity_type``, ``name``, and the source id columns
    (``spotify_id`` / ``itunes_id`` / ``deezer_id`` / ``musicbrainz_id``).
    Unknown entity types are ignored."""
    idx = BlocklistIndex()
    by_type = {ENTITY_ARTIST: idx.artists, ENTITY_ALBUM: idx.albums, ENTITY_TRACK: idx.tracks}
    for row in rows or []:
        ti = by_type.get((row.get("entity_type") or "").strip().lower())
        if ti is None:
            continue
        for source, col in SOURCE_ID_FIELDS.items():
            val = row.get(col)
            if val:
                ti.ids[source].add(str(val))
        name = row.get("name")
        if name:
            ti.names.add(_norm(name))
    return idx


def candidate_block_reason(
    index: BlocklistIndex,
    *,
    source: Optional[str],
    track_id: Any = None,
    track_name: Any = None,
    album_id: Any = None,
    album_name: Any = None,
    artists: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Tuple[str, str]]:
    """Return ``(entity_type, label)`` for the first cascade hit, else None.

    ``source`` is the metadata source the candidate IDs came from (the wishlist
    payload's provider). ``artists`` is a list of ``{'id', 'name'}`` dicts.
    Order matters only for the returned reason — any hit blocks."""
    if index.is_empty:
        return None

    # Track level — id only (names too ambiguous to ban across artists).
    if index.tracks.hit(source, track_id, track_name, use_name=False):
        return (ENTITY_TRACK, str(track_name or track_id or "track"))

    # Album level — id only.
    if index.albums.hit(source, album_id, album_name, use_name=False):
        return (ENTITY_ALBUM, str(album_name or album_id or "album"))

    # Artist level — id OR case-folded name (safe + covers the backfill window).
    for artist in artists or []:
        a_id = artist.get("id") if isinstance(artist, dict) else None
        a_name = artist.get("name") if isinstance(artist, dict) else artist
        if index.artists.hit(source, a_id, a_name, use_name=True):
            return (ENTITY_ARTIST, str(a_name or a_id or "artist"))

    return None
