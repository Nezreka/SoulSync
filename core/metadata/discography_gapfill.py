"""Discography gap-fill (#1067, QT3496) — "show me what my source is missing."

Metadata sources silo discographies: MusicBrainz may know 13 albums where
Deezer lists 3. Instead of a canonical cross-source merge (the identity
problem behind #765/#1064 — deliberately avoided), gap-fill is a VIEW-layer
union: the artist page renders the base source's discography untouched, and
this module computes which releases the OTHER sources know that the base
doesn't. Each gap card keeps its owning source and id, so clicking it flows
through the existing per-source machinery unchanged.

Dedup is deliberately conservative — when in doubt, show both:
  * same release = normalized title match AND compatible years
    (within ±1, or either year unknown)
  * normalization is casefold + whitespace/punctuation collapse ONLY —
    parenthetical edition markers are KEPT, so "Geogaddi" and
    "Geogaddi (Deluxe Edition)" stay distinct cards rather than being
    wrongly collapsed

Pure functions, no I/O — the endpoint feeds it card lists.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_PUNCT_RE = re.compile(r"[^\w\s()]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: Any) -> str:
    """Casefold, drop punctuation (keeping parens — they mark editions),
    collapse whitespace."""
    text = str(title or "").casefold()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def release_year(card: Dict[str, Any]) -> Optional[int]:
    """Best-effort year from an artist-detail release card."""
    y = card.get("year")
    if y is None:
        rd = str(card.get("release_date") or "")
        y = rd[:4] if len(rd) >= 4 else None
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None
    return y if 1000 <= y <= 3000 else None


def _title_of(card: Dict[str, Any]) -> str:
    return normalize_title(card.get("title") or card.get("name"))


def same_release(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Conservative same-album test: normalized titles equal AND years
    compatible (±1, or unknown on either side)."""
    ta, tb = _title_of(a), _title_of(b)
    if not ta or ta != tb:
        return False
    ya, yb = release_year(a), release_year(b)
    if ya is None or yb is None:
        return True
    return abs(ya - yb) <= 1


def _track_count(card: Dict[str, Any]) -> Optional[int]:
    for key in ("total_tracks", "track_count"):
        try:
            n = int(card.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


# a single is this many tracks at most, an album this many at least. between
# the two, a count says nothing about which release it is
_SINGLE_MAX_TRACKS = 3
_ALBUM_MIN_TRACKS = 6


def clearly_not_the_same_size(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """A title-track single and its album share a title and a year. When both
    sides say how many tracks they hold, a one to three track release is not a
    six track one (#2050: an album another source lists was dropped as a
    duplicate of the single the page already had). Unknown counts say nothing."""
    ca, cb = _track_count(a), _track_count(b)
    if ca is None or cb is None:
        return False
    return min(ca, cb) <= _SINGLE_MAX_TRACKS and max(ca, cb) >= _ALBUM_MIN_TRACKS


def find_gap_releases(
    base_cards: Iterable[Dict[str, Any]],
    source_cards: Dict[str, List[Dict[str, Any]]],
    source_order: Iterable[str],
) -> List[Dict[str, Any]]:
    """Releases the base source doesn't have, walked in ``source_order``.

    Each returned card is the OTHER source's card verbatim plus
    ``gap_source``. A release two extra sources both know appears once
    (first source in the order wins it). Cards that match anything in the
    base — or an already-taken gap — are dropped."""
    taken: List[Dict[str, Any]] = list(base_cards)
    gaps: List[Dict[str, Any]] = []
    for source in source_order:
        for card in source_cards.get(source, []) or []:
            if not _title_of(card):
                continue
            if any(same_release(card, existing) for existing in taken):
                continue
            gap = dict(card)
            gap["gap_source"] = source
            gaps.append(gap)
            taken.append(gap)
    return gaps


def gap_fill_buckets(
    base: Dict[str, List[Dict[str, Any]]],
    others: Dict[str, Dict[str, List[Dict[str, Any]]]],
    source_order: Iterable[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket-aware wrapper over ``find_gap_releases``.

    ``base`` and each ``others[source]`` are artist-detail discography dicts
    ({'albums': [...], 'eps': [...], 'singles': [...]}). Dedup runs against
    the WHOLE base catalog for every bucket — sources disagree about what's
    an EP vs a single vs an album (#1064 territory), and a release the base
    lists as an album must not reappear as a gap "single" just because
    another source filed it differently. Each gap lands in the bucket its
    OWN source assigned."""
    # (bucket, card): the bucket is only consulted to tell a single from the
    # album named after it, never to keep two filings of one release apart
    taken: List[tuple] = []
    for bucket in ("albums", "eps", "singles"):
        taken.extend((bucket, card) for card in base.get(bucket) or [])

    def _held(bucket: str, card: Dict[str, Any]) -> bool:
        for other_bucket, existing in taken:
            if not same_release(card, existing):
                continue
            if other_bucket != bucket and clearly_not_the_same_size(card, existing):
                continue
            return True
        return False

    out: Dict[str, List[Dict[str, Any]]] = {"albums": [], "eps": [], "singles": []}
    for source in source_order:
        source_disc = others.get(source) or {}
        for bucket in ("albums", "eps", "singles"):
            for card in source_disc.get(bucket) or []:
                if not _title_of(card):
                    continue
                if _held(bucket, card):
                    continue
                gap = dict(card)
                gap["gap_source"] = source
                out[bucket].append(gap)
                taken.append((bucket, gap))
    return out
