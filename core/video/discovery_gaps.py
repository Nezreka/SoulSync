"""The "what am I missing?" gap engine for video discovery.

Two pure diffs that power the discover rails:

* **collection_gaps** — given the TMDB items of a franchise the user has *started*
  (owns >=1 of) and the set of tmdb ids they already own, return the franchise
  entries they're missing ("Complete the Matrix Collection — you're missing 2").
* **filmography_gaps** — given a person's credits (combined_credits) and the owned
  set, return the titles of theirs the user doesn't have ("More from Christopher
  Nolan — 3 you don't own").

Both are pure (no I/O, no TMDB, no DB) — the discover API wires them to real data
(``owned tmdb ids`` from the library, collection items from the TMDB client, person
credits from the enrichment engine), so the diff + ranking logic is unit-testable in
isolation. Items are plain dicts carrying at least ``tmdb_id``; ``kind`` /
``popularity`` / ``vote_count`` are used for filtering + ranking when present.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


def _owned_set(owned_tmdb_ids: Iterable) -> Set[int]:
    out: Set[int] = set()
    for x in owned_tmdb_ids or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def collection_gaps(owned_tmdb_ids: Iterable, collection_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Franchise entries the user is missing.

    ``collection_items`` is the full ordered film list of a TMDB collection (each a
    dict with ``tmdb_id``). Returns the entries whose tmdb id isn't in
    ``owned_tmdb_ids``, in the collection's original order (chronological / as TMDB
    returns it), deduped. Entries without a usable tmdb id are skipped.
    """
    owned = _owned_set(owned_tmdb_ids)
    seen: Set[int] = set()
    out: List[Dict[str, Any]] = []
    for it in collection_items or []:
        if not isinstance(it, dict):
            continue
        tid = it.get("tmdb_id")
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if tid in owned or tid in seen:
            continue
        seen.add(tid)
        out.append(it)
    return out


def filmography_gaps(
    owned_tmdb_ids: Iterable,
    credits: List[Dict[str, Any]],
    *,
    kinds: Iterable[str] = ("movie",),
    min_vote_count: int = 0,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    """Titles from a person's credits the user doesn't own, ranked by popularity.

    ``credits`` is a person's combined credits (each a dict with ``tmdb_id`` and
    ``kind``). Keeps only the requested ``kinds``, drops owned + duplicate ids, and
    (when ``min_vote_count`` > 0) filters out obscure entries lacking enough votes —
    so the rail surfaces real films, not every uncredited cameo. Sorted by
    ``popularity`` (then ``vote_count``) descending; ``limit`` caps the result (0 = all).
    """
    owned = _owned_set(owned_tmdb_ids)
    wanted = {str(k) for k in kinds}
    seen: Set[int] = set()
    out: List[Dict[str, Any]] = []
    for c in credits or []:
        if not isinstance(c, dict):
            continue
        if c.get("kind") is not None and str(c.get("kind")) not in wanted:
            continue
        tid = c.get("tmdb_id")
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if tid in owned or tid in seen:
            continue
        if min_vote_count and (c.get("vote_count") or 0) < min_vote_count:
            continue
        seen.add(tid)
        out.append(c)

    out.sort(key=lambda x: ((x.get("popularity") or 0), (x.get("vote_count") or 0)), reverse=True)
    return out[:limit] if limit and limit > 0 else out


__all__ = ["collection_gaps", "filmography_gaps"]
