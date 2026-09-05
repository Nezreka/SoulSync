"""Because You Listen To - seed identity, candidate selection, generation.

pure and testable. no db, no network, no config. the scanner hands in rows,
this decides which tracks land on which shelf and shapes the record that gets
stored as ONE generation.

three things the old inline builder got wrong, and what changed:

  - seed identity. similar_artists rows are keyed by a SOURCE id, and the old
    lookup keyed them by the watchlist ROW id, so an edge only resolved if the
    artist was watched AND the two id namespaces happened to collide. identity
    here is a (provider, id) PAIR, so a deezer id can never match an itunes
    one, and seeds resolve through the library catalogue, not the watchlist.

  - selection. the old code walked the pool in insertion order and kept the
    first 15 matches, so one freshly ingested album owned the whole shelf.
    candidates are now collected per related artist with a budget, scored
    (direct relationship always above genre fallback), then capped per artist
    and per album.

  - shelves. two seeds selected from the same insertion-ordered list
    independently and came out 90% identical. candidates are allocated to
    their strongest seed first; the other shelf backfills from what is left.

nothing here pads a shelf to look full. short supply produces a short shelf
with an honest presentation flag, and the diagnostics say how many candidates
survived each filter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.discovery.listening_recommendations import similarity_from_rank

SCHEMA_VERSION = 1
ALGORITHM = "bylt-v1"

# product defaults. proposed, not claimed optimal - they are the numbers the
# acceptance tests pin, and they move together with those tests.
SHELF_SIZE = 10
MAX_PER_ARTIST = 2
MAX_PER_ALBUM = 1
MIN_DISTINCT_ARTISTS = 4
MIN_SHELF_TRACKS = 3        # below this the shelf is 'insufficient', never padded
FULL_SHELF_TRACKS = 8       # at or above this, with enough artists, it renders full
PER_RELATED_ARTIST_BUDGET = 6
MAX_RELATED_ARTISTS = 40
MAX_SHELVES = 3

# direct relationships live in [1.0, 2.0], genre fallback in [0.0, 1.0), so a
# direct edge can never lose to a genre match no matter how specific the tag.
DIRECT_BASE = 1.0
GENRE_BASE = 1.0
# tracks keep their pool order inside one artist's budget; the decay is small
# enough that it never crosses a relationship boundary.
POSITION_DECAY = 0.001

# the id columns each row type carries, paired with the provider that owns them
ARTIST_ID_COLUMNS = (
    ("spotify", "spotify_artist_id"),
    ("itunes", "itunes_artist_id"),
    ("deezer", "deezer_id"),
    ("musicbrainz", "musicbrainz_id"),
)
WATCHLIST_ID_COLUMNS = (
    ("spotify", "spotify_artist_id"),
    ("itunes", "itunes_artist_id"),
    ("deezer", "deezer_artist_id"),
    ("discogs", "discogs_artist_id"),
    ("musicbrainz", "musicbrainz_id"),
)

# why an edge was accepted or dropped. kept as data so the diagnostics can
# report it and a test can assert on it.
EDGE_PROVIDER_MATCH = "provider"
EDGE_LEGACY_PROVABLE = "legacy-provable"
EDGE_LEGACY_AMBIGUOUS = "legacy-ambiguous"
EDGE_NO_MATCH = "no-match"


def norm(text: Any) -> str:
    return str(text or "").strip().lower()


def normalize_title(title: Any) -> str:
    """casefold + collapse whitespace, KEEPING every qualifier.

    a remix, a live cut and a slowed edit are different recordings, so the
    parenthetical stays in the key. only case and spacing are normalised.
    """
    return " ".join(str(title or "").lower().split())


def recording_key(artist: Any, title: Any) -> Tuple[str, str]:
    return (norm(artist), normalize_title(title))


def album_key(artist: Any, album: Any) -> Tuple[str, str]:
    """canonical album identity. keyed by artist too - two artists may both
    have an album called 'Greatest Hits' and they are not the same record."""
    return (norm(artist), normalize_title(album))


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return getattr(row, key, None)


# ── seed identity ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeedIdentity:
    """one listening seed and every provider id that proves who it is."""

    name: str
    ids: Tuple[Tuple[str, str], ...] = ()

    @property
    def norm_name(self) -> str:
        return norm(self.name)

    @property
    def key(self) -> str:
        """stable identity for the stored section. a provider pair when we have
        one, otherwise the name - ordinals are never an identity."""
        if self.ids:
            provider, ident = self.ids[0]
            return f"{provider}:{ident}"
        return f"name:{self.norm_name}"

    @property
    def bare_ids(self) -> set:
        return {ident for _, ident in self.ids}


def collect_identities(
    artist_rows: Sequence[Any] = (),
    watchlist_rows: Sequence[Any] = (),
) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, set]]:
    """-> ({artist_lower: [(provider, id)]}, {bare_id: {artist_lower, ...}}).

    the library catalogue is the primary mapping; the watchlist is an
    ADDITIONAL source, never a prerequisite - listening to an artist does not
    imply watching it, and that assumption is what made these shelves fail.

    the second map records who else claims each bare id, which is the only way
    a legacy edge with no provider can be resolved honestly.
    """
    by_name: Dict[str, List[Tuple[str, str]]] = {}
    ownership: Dict[str, set] = {}

    def _absorb(rows, columns, name_col):
        for row in rows or ():
            name = norm(_row_get(row, name_col))
            if not name:
                continue
            bucket = by_name.setdefault(name, [])
            for provider, column in columns:
                raw = _row_get(row, column)
                if raw is None or str(raw).strip() == "":
                    continue
                pair = (provider, str(raw).strip())
                if pair not in bucket:
                    bucket.append(pair)
                ownership.setdefault(pair[1], set()).add(name)

    _absorb(artist_rows, ARTIST_ID_COLUMNS, "name")
    _absorb(watchlist_rows, WATCHLIST_ID_COLUMNS, "artist_name")
    return by_name, ownership


def seed_identities(
    seed_names: Sequence[Any],
    by_name: Dict[str, List[Tuple[str, str]]],
) -> List[SeedIdentity]:
    """seed names (in play order) -> identities, duplicates collapsed.

    a seed with no known ids still gets an identity: it can only reach the
    genre fallback, but it is never silently dropped and never shares an
    ordinal with another seed.
    """
    out: List[SeedIdentity] = []
    seen = set()
    for raw in seed_names or ():
        name = str(raw or "").strip()
        key = norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(SeedIdentity(name=name, ids=tuple(by_name.get(key, ()))))
    return out


def classify_edge(edge: Any, seed: SeedIdentity, ownership: Dict[str, set]) -> str:
    """why this edge does or does not belong to this seed.

    a row that records its provider is matched as a PAIR. a legacy row (no
    provider recorded) is accepted only when its bare id is claimed by exactly
    one artist and that artist is the seed - anything else is ambiguous and
    stays unusable rather than being guessed at by numeric equality.
    """
    ident = str(_row_get(edge, "source_artist_id") or "").strip()
    if not ident:
        return EDGE_NO_MATCH
    provider = norm(_row_get(edge, "source_provider"))
    if provider:
        return EDGE_PROVIDER_MATCH if (provider, ident) in seed.ids else EDGE_NO_MATCH
    if ident not in seed.bare_ids:
        return EDGE_NO_MATCH
    claimants = ownership.get(ident) or set()
    if claimants and claimants != {seed.norm_name}:
        return EDGE_LEGACY_AMBIGUOUS
    return EDGE_LEGACY_PROVABLE


def related_from_edges(
    seed: SeedIdentity,
    edges: Sequence[Any],
    ownership: Dict[str, set],
    *,
    max_related: int = MAX_RELATED_ARTISTS,
) -> Tuple[List[dict], Dict[str, int]]:
    """usable direct relationships for one seed, closest first.

    returns ``([{name, rank, relation, weight, detail}], counts)``. counts
    carries every classification so the shelf can report how much of its
    evidence was thrown away as ambiguous instead of pretending it never
    existed.
    """
    counts = {EDGE_PROVIDER_MATCH: 0, EDGE_LEGACY_PROVABLE: 0,
              EDGE_LEGACY_AMBIGUOUS: 0, EDGE_NO_MATCH: 0}
    best: Dict[str, dict] = {}
    for edge in edges or ():
        verdict = classify_edge(edge, seed, ownership)
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict not in (EDGE_PROVIDER_MATCH, EDGE_LEGACY_PROVABLE):
            continue
        name = str(_row_get(edge, "similar_artist_name") or "").strip()
        if not name or norm(name) == seed.norm_name:
            continue
        rank = _row_get(edge, "similarity_rank")
        weight = DIRECT_BASE + similarity_from_rank(rank)
        prior = best.get(norm(name))
        if prior and prior["weight"] >= weight:
            continue
        best[norm(name)] = {
            "name": name,
            "rank": rank,
            "relation": "direct",
            "weight": weight,
            "detail": name,
            "provenance": verdict,
        }
    related = sorted(best.values(), key=lambda r: (-r["weight"], norm(r["name"])))
    return related[:max_related], counts


# ── genre fallback ──────────────────────────────────────────────────────────


def genre_document_counts(genre_by_artist: Dict[str, Iterable[str]]) -> Dict[str, int]:
    """how many artists carry each genre. the denominator for specificity."""
    counts: Dict[str, int] = {}
    for genres in (genre_by_artist or {}).values():
        for g in {norm(g) for g in (genres or ()) if norm(g)}:
            counts[g] = counts.get(g, 0) + 1
    return counts


def genre_specificity(genre: Any, doc_counts: Dict[str, int], total: int) -> float:
    """0..1 - how much a shared tag actually says.

    plain idf. 'pop' sits on half the catalogue and scores near zero; a tag
    two artists share scores near one. an unknown tag scores 0, so it can
    never be dressed up as evidence.
    """
    key = norm(genre)
    if not key or total <= 1:
        return 0.0
    count = int((doc_counts or {}).get(key, 0))
    if count <= 0:
        return 0.0
    value = math.log(total / (1.0 + count)) / math.log(total)
    return max(0.0, min(1.0, round(value, 6)))


def shared_genre_match(
    seed_genres: Iterable[str],
    candidate_genres: Iterable[str],
    doc_counts: Dict[str, int],
    total: int,
) -> Tuple[float, Optional[str]]:
    """the strongest specific tag two artists share, and its weight."""
    shared = {norm(g) for g in (seed_genres or ()) if norm(g)} & {
        norm(g) for g in (candidate_genres or ()) if norm(g)
    }
    best, best_genre = 0.0, None
    for g in sorted(shared):
        score = genre_specificity(g, doc_counts, total)
        if score > best:
            best, best_genre = score, g
    return best, best_genre


def related_from_genres(
    seed: SeedIdentity,
    genre_by_artist: Dict[str, Iterable[str]],
    pool_artists: Iterable[str],
    doc_counts: Dict[str, int],
    *,
    max_related: int = MAX_RELATED_ARTISTS,
) -> List[dict]:
    """the fallback: artists in the pool who share a SPECIFIC tag with the seed.

    ordered by how specific that tag is, not by whatever the pool happened to
    hold first. a generic-only overlap scores near zero and will lose to any
    direct relationship, which is the point.
    """
    seed_genres = genre_by_artist.get(seed.norm_name) or ()
    if not seed_genres:
        return []
    total = len(genre_by_artist or {})
    out: List[dict] = []
    for artist in sorted({norm(a) for a in (pool_artists or ()) if norm(a)}):
        if artist == seed.norm_name:
            continue
        score, genre = shared_genre_match(
            seed_genres, genre_by_artist.get(artist) or (), doc_counts, total)
        if score <= 0.0 or not genre:
            continue
        out.append({"name": artist, "rank": None, "relation": "genre",
                    "weight": GENRE_BASE * score, "detail": genre,
                    "provenance": "genre"})
    out.sort(key=lambda r: (-r["weight"], r["name"]))
    return out[:max_related]


# ── candidates ──────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """one pool track offered to one seed, with why it was offered."""

    track: Dict[str, Any]
    seed_key: str
    relation: str
    relation_detail: str
    score: float
    # the stored similarity_rank of the edge that produced this candidate.
    # carried, not just consumed into the score, so the shelf can say HOW
    # close the relationship was and a later tune can re-weight it.
    relation_rank: Optional[int] = None
    artist: str = ""
    album: str = ""
    title: str = ""
    track_id: str = ""

    @property
    def recording(self) -> Tuple[str, str]:
        return recording_key(self.artist, self.title)

    @property
    def album_id(self) -> Tuple[str, str]:
        return album_key(self.artist, self.album)


def candidate_from_row(row: Dict[str, Any], seed_key: str, relation: str,
                       detail: str, score: float,
                       rank: Optional[int] = None) -> Candidate:
    return Candidate(
        track=row,
        seed_key=seed_key,
        relation=relation,
        relation_detail=detail,
        score=score,
        relation_rank=rank,
        artist=str(row.get("artist_name") or ""),
        album=str(row.get("album_name") or ""),
        title=str(row.get("track_name") or ""),
        track_id=str(row.get("track_id") or ""),
    )


def collect_candidates(
    seed: SeedIdentity,
    related: Sequence[dict],
    pool_by_artist: Dict[str, List[Dict[str, Any]]],
    *,
    budget: int = PER_RELATED_ARTIST_BUDGET,
) -> List[Candidate]:
    """walk the RELATIONSHIPS, not the pool.

    each related artist gets an explicit budget, so a 20-track album that
    landed in the pool this morning contributes at most ``budget`` candidates
    instead of filling the shelf before the second relationship is reached.
    """
    out: List[Candidate] = []
    seen = set()
    for rel in related or ():
        tracks = pool_by_artist.get(norm(rel.get("name"))) or []
        taken = 0
        for row in tracks:
            if taken >= budget:
                break
            key = recording_key(row.get("artist_name"), row.get("track_name"))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            score = float(rel.get("weight") or 0.0) - POSITION_DECAY * taken
            rank = rel.get("rank")
            out.append(candidate_from_row(
                row, seed.key, str(rel.get("relation") or "direct"),
                str(rel.get("detail") or rel.get("name") or ""), score,
                int(rank) if isinstance(rank, int) else None))
            taken += 1
    return out


def _sort_key(c: Candidate):
    # stable inside one generation: the same inputs always produce the same
    # order. refresh means regenerate, not reshuffle the same weak 15.
    return (-c.score, norm(c.artist), normalize_title(c.album),
            normalize_title(c.title), c.track_id)


def select_shelf(
    candidates: Sequence[Candidate],
    *,
    size: int = SHELF_SIZE,
    per_artist: int = MAX_PER_ARTIST,
    per_album: int = MAX_PER_ALBUM,
    exclude_recordings: Optional[set] = None,
    seed_selection: Optional[Sequence[Candidate]] = None,
) -> List[Candidate]:
    """greedy pick under artist and album caps, best score first.

    ``seed_selection`` continues an existing shelf (the backfill pass) so the
    caps hold across both passes rather than per pass.
    """
    chosen: List[Candidate] = list(seed_selection or [])
    by_artist: Dict[str, int] = {}
    by_album: Dict[Tuple[str, str], int] = {}
    used = set(exclude_recordings or set())
    for c in chosen:
        by_artist[norm(c.artist)] = by_artist.get(norm(c.artist), 0) + 1
        by_album[c.album_id] = by_album.get(c.album_id, 0) + 1
        used.add(c.recording)

    for c in sorted(candidates, key=_sort_key):
        if len(chosen) >= size:
            break
        if c.recording in used:
            continue
        if by_artist.get(norm(c.artist), 0) >= per_artist:
            continue
        if c.album and by_album.get(c.album_id, 0) >= per_album:
            continue
        chosen.append(c)
        used.add(c.recording)
        by_artist[norm(c.artist)] = by_artist.get(norm(c.artist), 0) + 1
        by_album[c.album_id] = by_album.get(c.album_id, 0) + 1
    return chosen


def presentation_for(selected: Sequence[Candidate], *,
                     min_artists: int = MIN_DISTINCT_ARTISTS,
                     full_at: int = FULL_SHELF_TRACKS,
                     min_tracks: int = MIN_SHELF_TRACKS) -> str:
    """'full' | 'compact' | 'insufficient'.

    a shelf that cannot reach the diversity floor is not promoted to a full
    horizontal row. one card never gets a shelf's height by accident.
    """
    if len(selected) < min_tracks:
        return "insufficient"
    artists = len({norm(c.artist) for c in selected})
    if len(selected) >= full_at and artists >= min_artists:
        return "full"
    return "compact"


def shelf_reason(seed_name: str, selected: Sequence[Candidate]) -> dict:
    """the short, truthful why. built from what was actually selected.

    no provider is quoted and no relationship is invented: a genre shelf says
    which tag it shares, a direct shelf names the relationship, and a shelf
    with nothing to say says only that it came from your listening.
    """
    kinds = {c.relation for c in selected}
    direct = [c.relation_detail for c in selected if c.relation == "direct"]
    genres = [c.relation_detail for c in selected if c.relation == "genre"]
    if kinds == {"direct"}:
        return {"kind": "direct",
                "label": f"Artists similar to {seed_name}",
                "evidence": sorted(set(direct))[:3]}
    if kinds == {"genre"}:
        top = genres[0] if genres else None
        return {"kind": "genre",
                "label": (f"Shares {top} with {seed_name}" if top
                          else f"From your {seed_name} listening"),
                "evidence": sorted(set(genres))[:3]}
    if kinds:
        top = genres[0] if genres else None
        return {"kind": "mixed",
                "label": (f"Similar artists and shared {top}" if top
                          else f"Artists similar to {seed_name}"),
                "evidence": sorted(set(direct))[:2] + sorted(set(genres))[:1]}
    return {"kind": "none", "label": f"From your {seed_name} listening",
            "evidence": []}


@dataclass
class Shelf:
    seed: SeedIdentity
    selected: List[Candidate] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def allocate_shelves(
    per_seed: Sequence[Tuple[SeedIdentity, Sequence[Candidate]]],
    *,
    size: int = SHELF_SIZE,
    per_artist: int = MAX_PER_ARTIST,
    per_album: int = MAX_PER_ALBUM,
) -> List[Shelf]:
    """allocate candidates ACROSS the visible shelves, not per shelf alone.

    a recording is offered first to the seed that scores it highest; the other
    shelves then backfill from what nobody claimed. nothing is ever placed on
    two shelves: a shelf that could only be filled by repeating another one has
    no evidence of its own, and a short shelf is the honest answer. the
    generation drops it rather than showing a heading over one borrowed card.
    """
    owner: Dict[Tuple[str, str], Tuple[float, int]] = {}
    for idx, (_seed, cands) in enumerate(per_seed):
        for c in cands:
            best = owner.get(c.recording)
            if best is None or c.score > best[0]:
                owner[c.recording] = (c.score, idx)

    shelves: List[Shelf] = []
    claimed: set = set()
    for idx, (seed, cands) in enumerate(per_seed):
        mine = [c for c in cands if owner.get(c.recording, (0.0, idx))[1] == idx]
        picked = select_shelf(mine, size=size, per_artist=per_artist,
                              per_album=per_album, exclude_recordings=claimed)
        shelves.append(Shelf(seed=seed, selected=picked, diagnostics={
            "candidates": len(cands),
            "owned": len(mine),
            "after_caps": len(picked),
            "backfilled": 0,
            "overlap": 0,
        }))
        claimed.update(c.recording for c in picked)

    # pass 2: fill the short shelves from unclaimed candidates
    for idx, (_seed, cands) in enumerate(per_seed):
        shelf = shelves[idx]
        if len(shelf.selected) >= size:
            continue
        spare = [c for c in cands if c.recording not in claimed]
        before = len(shelf.selected)
        shelf.selected = select_shelf(
            spare, size=size, per_artist=per_artist, per_album=per_album,
            seed_selection=shelf.selected)
        shelf.diagnostics["backfilled"] = len(shelf.selected) - before
        claimed.update(c.recording for c in shelf.selected)

    for shelf in shelves:
        shelf.diagnostics.update({
            "selected": len(shelf.selected),
            "distinct_artists": len({norm(c.artist) for c in shelf.selected}),
            "distinct_albums": len({c.album_id for c in shelf.selected}),
        })
    return shelves


def shelf_overlap(a: Sequence[Candidate], b: Sequence[Candidate]) -> int:
    """exact-recording overlap between two shelves. the number the audit
    measured at 9/10 and the gate keeps at 0 while supply allows."""
    return len({c.recording for c in a} & {c.recording for c in b})


# ── the stored generation ───────────────────────────────────────────────────


def section_from_shelf(shelf: Shelf, *, seed_image: Optional[str] = None) -> dict:
    """one shelf as the record that gets stored and served.

    the heading, the reason and the tracks live in ONE scoped object keyed by
    seed identity. nothing about this section is addressed by an ordinal, so a
    rank change can never leave last week's shelf standing next to this
    week's.
    """
    tracks = []
    for c in shelf.selected:
        row = dict(c.track)
        row["relation"] = c.relation
        row["relation_detail"] = c.relation_detail
        row["relation_rank"] = c.relation_rank
        row["seed_key"] = shelf.seed.key
        tracks.append(row)
    return {
        "seed_key": shelf.seed.key,
        "seed_name": shelf.seed.name,
        "seed_ids": [list(pair) for pair in shelf.seed.ids],
        "seed_image": seed_image,
        "reason": shelf_reason(shelf.seed.name, shelf.selected),
        "presentation": presentation_for(shelf.selected),
        "diagnostics": dict(shelf.diagnostics),
        "tracks": tracks,
    }


def build_generation(
    sections: Sequence[dict],
    *,
    profile_id: int,
    source: str,
    generation_id: str,
    generated_at: str,
    status: str = "ok",
) -> dict:
    """the whole visible set, versioned and scoped, as one value.

    it is written in a single store call so a reader sees either the old
    complete generation or the new one. a successful generation with no
    sections is stored exactly like that - an explicit empty state, not a
    silent survival of last week's shelves.
    """
    # a section with too little to say is dropped, not padded and not shown as
    # a heading over one card. that one-card shelf under a repeated heading is
    # the reported symptom, and it is a storage decision, not a css one.
    kept = [s for s in sections
            if s.get("tracks") and s.get("presentation") != "insufficient"]
    return {
        "schema": SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "generation_id": generation_id,
        "profile_id": profile_id,
        "source": source,
        "generated_at": generated_at,
        "status": status,
        "sections": kept[:MAX_SHELVES],
    }


def validate_generation(gen: Any) -> bool:
    """is this a generation this code can serve?

    a duplicate seed inside one generation is a validation FAILURE, not
    something to hide in the renderer - it is exactly the bug the ordinal
    slots produced.
    """
    if not isinstance(gen, dict):
        return False
    if gen.get("schema") != SCHEMA_VERSION:
        return False
    sections = gen.get("sections")
    if not isinstance(sections, list):
        return False
    keys = [s.get("seed_key") for s in sections if isinstance(s, dict)]
    if len(keys) != len(sections):
        return False
    if len(set(keys)) != len(keys):
        return False
    for s in sections:
        if not isinstance(s.get("tracks"), list):
            return False
    return True


__all__ = [
    "ALGORITHM",
    "Candidate",
    "EDGE_LEGACY_AMBIGUOUS",
    "EDGE_LEGACY_PROVABLE",
    "EDGE_NO_MATCH",
    "EDGE_PROVIDER_MATCH",
    "FULL_SHELF_TRACKS",
    "MAX_PER_ALBUM",
    "MAX_PER_ARTIST",
    "MAX_SHELVES",
    "MIN_DISTINCT_ARTISTS",
    "MIN_SHELF_TRACKS",
    "PER_RELATED_ARTIST_BUDGET",
    "SCHEMA_VERSION",
    "SHELF_SIZE",
    "SeedIdentity",
    "Shelf",
    "album_key",
    "allocate_shelves",
    "build_generation",
    "candidate_from_row",
    "classify_edge",
    "collect_candidates",
    "collect_identities",
    "genre_document_counts",
    "genre_specificity",
    "normalize_title",
    "presentation_for",
    "recording_key",
    "related_from_edges",
    "related_from_genres",
    "section_from_shelf",
    "seed_identities",
    "select_shelf",
    "shared_genre_match",
    "shelf_overlap",
    "shelf_reason",
    "validate_generation",
]
