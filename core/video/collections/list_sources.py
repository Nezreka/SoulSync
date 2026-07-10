"""Build the injected ``list_fetcher`` the resolver/sync use to learn a list
collection's FULL membership (so it can compute the members you don't own yet).

Sources (all TMDB — the key the video side already has):
  · franchise / tmdb_collection — the films of a TMDB collection id.
  · tmdb_chart   — a living chart: ref {chart, limit}. Charts: top_movies /
    popular_movies / now_playing / top_shows / popular_shows / on_the_air
    (paged curated lists) + trending_movies / trending_shows. 'Top Rated 250'
    is the IMDb-Top-250 equivalent; membership re-resolves on every sync.
  · tmdb_keyword — themed/seasonal: ref {kind, query, limit}. The keyword NAME
    ('christmas', 'based on comic') resolves to its TMDB id at runtime — no
    hardcoded ids to rot.
  · tmdb_list    — a public TMDB list id (paged).
  · trakt_list   — still deferred (needs a Trakt client); resolves owned-only.

Everything is wired through the enrichment engine (shared 1h cache), and the
engine is injectable so the resolver/sync/presets stay unit-testable without
network.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.list_sources")

_FRANCHISE = {"tmdb_collection", "franchise"}

# chart key -> (engine surface, curated key / trending kind)
_CHARTS: Dict[str, tuple] = {
    "top_movies":      ("curated", "top_movies"),
    "popular_movies":  ("curated", "popular_movies"),
    "now_playing":     ("curated", "now_playing"),
    "top_shows":       ("curated", "top_shows"),
    "popular_shows":   ("curated", "popular_shows"),
    "on_the_air":      ("curated", "on_the_air"),
    "trending_movies": ("trending", "movie"),
    "trending_shows":  ("trending", "show"),
}
_MAX_PAGES = 15          # hard page cap (15×20 = 300 items) whatever the limit says
_DEFAULT_LIMIT = 100


def chart_keys(media_type: str) -> List[str]:
    """The chart keys valid for a media type (for the editor's chart picker)."""
    movie = ["top_movies", "popular_movies", "trending_movies", "now_playing"]
    show = ["top_shows", "popular_shows", "trending_shows", "on_the_air"]
    return movie if media_type == "movie" else show


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
    try:
        year = int(year) if year is not None else None
    except (ValueError, TypeError):
        year = None
    return {
        "tmdb_id": int(tid),
        "title": item.get("title") or item.get("name"),
        "year": year,
        "poster_url": item.get("poster_url") or item.get("poster") or item.get("poster_path"),
    }


def _dedup_normed(items) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for it in items:
        n = _norm(it) if it else None
        if n and n["tmdb_id"] not in seen:
            seen.add(n["tmdb_id"])
            out.append(n)
    return out


def _limit_of(ref: Any) -> int:
    try:
        n = int((ref or {}).get("limit") or _DEFAULT_LIMIT)
    except (AttributeError, TypeError, ValueError):
        n = _DEFAULT_LIMIT
    return max(1, min(n, _MAX_PAGES * 20))


def _fetch_pages(fetch_page: Callable, limit: int) -> list:
    """Fetch the pages a limit needs CONCURRENTLY (a 250-item chart is 13 TMDB
    round-trips — sequential paging is why 'Easy setup' crawled on first open).
    ``ex.map`` preserves page order, so chart RANK ordering survives."""
    pages = range(1, min(_MAX_PAGES, (limit + 19) // 20) + 1)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        batches = list(ex.map(lambda p: fetch_page(p) or [], pages))
    return [it for batch in batches for it in batch]


def _fetch_chart(eng, ref: Any) -> List[Dict[str, Any]]:
    chart = str((ref or {}).get("chart") or "") if isinstance(ref, dict) else str(ref or "")
    spec = _CHARTS.get(chart)
    if not spec:
        logger.debug("unknown chart %r", chart)
        return []
    limit = _limit_of(ref if isinstance(ref, dict) else None)
    surface, key = spec
    if surface == "trending":
        return _dedup_normed(eng.trending(window="week", kind=key) or [])[:limit]
    raw = _fetch_pages(lambda p: eng.discover_curated(key, page=p), limit)
    return _dedup_normed(raw)[:limit]


def _fetch_keyword(eng, ref: Any) -> List[Dict[str, Any]]:
    if not isinstance(ref, dict):
        return []
    query = str(ref.get("query") or "").strip()
    kind = "show" if ref.get("kind") == "show" else "movie"
    if not query:
        return []
    kid = eng.keyword_id(query)
    if not kid:
        logger.debug("no TMDB keyword for %r", query)
        return []
    limit = _limit_of(ref)
    raw = _fetch_pages(
        lambda p: eng.discover_filter(kind, keywords=str(kid), page=p, vote_count_min=20),
        limit)
    return _dedup_normed(raw)[:limit]


def _fetch_union(eng, ref: Any) -> List[Dict[str, Any]]:
    """Universe collections — the UNION of several TMDB franchises and/or
    keyword themes (the MCU isn't a TMDB collection; Middle-earth is LOTR +
    The Hobbit). ref: {collections: [ids], keywords: [queries], kind, limit}."""
    if not isinstance(ref, dict):
        return []
    kind = "show" if ref.get("kind") == "show" else "movie"
    limit = _limit_of(ref)
    raw: list = []
    for cid in ref.get("collections") or []:
        try:
            raw.extend(eng.collection(int(cid)) or [])
        except (TypeError, ValueError):
            continue
    for q in ref.get("keywords") or []:
        raw.extend(_fetch_keyword(eng, {"kind": kind, "query": q, "limit": limit}))
    return _dedup_normed(raw)[: _MAX_PAGES * 20]


def _fetch_list(eng, ref: Any) -> List[Dict[str, Any]]:
    raw: list = []
    page, total = 1, 1
    while page <= min(total, _MAX_PAGES):
        items, total = eng.list_page(ref, page=page)
        if not items:
            break
        raw.extend(items)
        page += 1
    return _dedup_normed(raw)


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
        try:
            if source in _FRANCHISE:
                items = eng.collection(int(ref)) or []
                return _dedup_normed(items)
            if source == "tmdb_chart":
                return _fetch_chart(eng, ref)
            if source == "tmdb_keyword":
                return _fetch_keyword(eng, ref)
            if source == "tmdb_union":
                return _fetch_union(eng, ref)
            if source == "tmdb_list":
                return _fetch_list(eng, ref)
        except Exception:   # noqa: BLE001 - membership is best-effort; owned still syncs
            logger.debug("list fetch failed for %s %r", source, ref, exc_info=True)
            return []
        # trakt_list — deferred (needs a Trakt client).
        logger.debug("list source %r not yet fetchable; owned-only", source)
        return []

    return fetch


__all__ = ["build_list_fetcher", "chart_keys"]
