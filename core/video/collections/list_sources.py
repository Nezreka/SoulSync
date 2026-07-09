"""Build the injected ``list_fetcher`` the resolver/sync use to learn a list
collection's FULL membership (so it can compute the members you don't own yet).

Franchise (TMDB collection) is wired through the existing enrichment engine
(``eng.collection(id)``, cached + owned-annotated). TMDB/Trakt lists are UI-wired
but their remote fetch is deferred — they resolve to an empty set for now (the
owned intersection is still correct, there's just nothing to wishlist yet).
Kept separate + injected so the resolver/sync stay unit-testable without network.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.list_sources")

_FRANCHISE = {"tmdb_collection", "franchise"}


def _norm(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tid = item.get("tmdb_id") if item.get("tmdb_id") is not None else item.get("id")
    if tid is None:
        return None
    year = item.get("year")
    if year is None and item.get("release_date"):
        try:
            year = int(str(item["release_date"])[:4])
        except (ValueError, TypeError):
            year = None
    return {
        "tmdb_id": int(tid),
        "title": item.get("title") or item.get("name"),
        "year": year,
        "poster_url": item.get("poster_url") or item.get("poster_path"),
    }


def build_list_fetcher(db=None, *, engine_factory: Optional[Callable] = None) -> Callable:
    """Return ``fetch(source, ref) -> [{tmdb_id,title,year,poster_url}]``.

    ``engine_factory`` is injectable for tests; by default it lazily resolves the
    shared video enrichment engine.
    """
    def _engine():
        if engine_factory is not None:
            return engine_factory()
        from core.video.enrichment.engine import get_video_enrichment_engine
        return get_video_enrichment_engine()

    def fetch(source: str, ref: Any) -> List[Dict[str, Any]]:
        source = str(source or "").lower()
        try:
            eng = _engine()
        except Exception:   # noqa: BLE001 - no engine → no membership, owned still syncs
            logger.debug("list fetcher: engine unavailable", exc_info=True)
            return []
        if eng is None:
            return []
        if source in _FRANCHISE:
            try:
                items = eng.collection(int(ref)) or []
            except Exception:   # noqa: BLE001
                logger.debug("franchise fetch failed for %s", ref, exc_info=True)
                return []
            return [n for n in (_norm(it) for it in items) if n]
        # tmdb_list / trakt_list — deferred (no client list endpoint yet).
        logger.debug("list source %r not yet fetchable; owned-only", source)
        return []

    return fetch


__all__ = ["build_list_fetcher"]
