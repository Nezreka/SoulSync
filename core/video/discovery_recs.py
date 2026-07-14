"""Blend per-title TMDB recommendations into one ranked "Recommended for you" wall.

The discover page already does per-title rails ("More like Dune"). This aggregates the
recommendations of MANY owned titles into a single personalized wall: a candidate
recommended by *more* of your titles ranks higher (consensus is a stronger signal than
any one seed), ties broken by rating then popularity. Owned titles (the engine annotates
each recommendation with ``library_id`` when owned) and the seed titles themselves are
excluded, so the wall is all stuff you don't have.

Pure + I/O-free: the discover API fetches each seed's recommendations (cached) and passes
the lists here, so the dedup/consensus ranking is unit-testable without TMDB.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def blend_recommendations(
    rec_lists: List[List[Dict[str, Any]]],
    *,
    exclude_ids: Iterable = (),
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Aggregate ``rec_lists`` (one recommendation list per seed title) into a single
    ranked, deduped list of un-owned titles.

    Each item is a dict with ``tmdb_id`` / ``kind`` (and optionally ``library_id`` set by
    the engine when owned, plus ``rating`` / ``popularity``). A title appearing across more
    seed lists scores higher; ties fall back to rating then popularity. Owned items
    (``library_id`` not None) and ``exclude_ids`` (the seeds) are dropped. ``limit`` caps
    the result (0 = all).
    """
    exclude = set()
    for x in exclude_ids or []:
        try:
            exclude.add(int(x))
        except (TypeError, ValueError):
            continue

    agg: Dict[tuple, Dict[str, Any]] = {}
    for lst in rec_lists or []:
        for it in lst or []:
            if not isinstance(it, dict):
                continue
            if it.get("library_id") is not None:   # owned (engine-annotated) — skip
                continue
            tid = it.get("tmdb_id")
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                continue
            if tid in exclude:
                continue
            key = (it.get("kind"), tid)
            entry = agg.get(key)
            if entry is None:
                agg[key] = {"item": it, "count": 1}
            else:
                entry["count"] += 1

    ranked = sorted(
        agg.values(),
        key=lambda e: (e["count"], e["item"].get("rating") or 0, e["item"].get("popularity") or 0),
        reverse=True,
    )
    items = [e["item"] for e in ranked]
    return items[:limit] if limit and limit > 0 else items


__all__ = ["blend_recommendations"]
