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
    # Low vote floor (10, not discover's default 40): an owned deep-cut must
    # still match its theme — junk is filtered by the popularity sort + limit.
    raw = _fetch_pages(
        lambda p: eng.discover_filter(kind, keywords=str(kid), page=p, vote_count_min=10),
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


# ── IMDb (keyless — via IMDb's own public GraphQL endpoint) ─────────────────
# IMDb has no documented API and the HTML is behind a bot-wall (202 + empty
# body), so like current Kometa we use api.graphql.imdb.com — the endpoint
# imdb.com itself calls, keyless. Charts come from chartTitles; user lists
# (ls…) from list.titleListItemSearch (cursor-paginated). tt-ids then map →
# TMDB via /find (day-cached per id, pooled). Unofficial: every step is
# best-effort and degrades to owned-only, never an error.
_IMDB_GQL = "https://api.graphql.imdb.com/"
_IMDB_CHARTS = {
    "top":     "TOP_RATED_MOVIES",        # IMDb Top 250 (movies)
    "popular": "MOST_POPULAR_MOVIES",     # Most Popular Movies
    "toptv":   "TOP_RATED_TV_SHOWS",      # Top 250 TV
    "tvmeter": "MOST_POPULAR_TV_SHOWS",   # Most Popular TV
}
_TITLE_FIELDS = "id titleText { text } releaseYear { year }"


def _imdb_graphql(query: str) -> dict:
    import requests
    r = requests.post(_IMDB_GQL, json={"query": query},
                      headers={"Content-Type": "application/json",
                               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                      timeout=30)
    r.raise_for_status()
    return r.json() or {}


def _imdb_pairs_chart(chart_type: str) -> List[tuple]:
    d = _imdb_graphql(
        "query { chartTitles(first: 250, chart: {chartType: %s}) "
        "{ edges { node { %s } } } }" % (chart_type, _TITLE_FIELDS))
    out = []
    for e in (((d.get("data") or {}).get("chartTitles") or {}).get("edges")) or []:
        n = e.get("node") or {}
        if n.get("id"):
            out.append((n["id"], ((n.get("titleText") or {}).get("text")),
                        (n.get("releaseYear") or {}).get("year")))
    return out


def _imdb_pairs_list(ls_id: str) -> List[tuple]:
    out = []
    cursor = ""
    for _ in range(4):                        # 4×250 = 1000 items, plenty
        after = ', after: "%s"' % cursor if cursor else ""
        d = _imdb_graphql(
            'query { list(id: "%s") { titleListItemSearch(first: 250%s) '
            "{ edges { title { %s } } pageInfo { hasNextPage endCursor } } } }"
            % (ls_id, after, _TITLE_FIELDS))
        search = (((d.get("data") or {}).get("list") or {}).get("titleListItemSearch")) or {}
        for e in search.get("edges") or []:
            t = e.get("title") or {}
            if t.get("id"):
                out.append((t["id"], ((t.get("titleText") or {}).get("text")),
                            (t.get("releaseYear") or {}).get("year")))
        page = search.get("pageInfo") or {}
        cursor = page.get("endCursor") or ""
        if not page.get("hasNextPage") or not cursor:
            break
    return out


def _fetch_imdb(eng, ref: Any) -> List[Dict[str, Any]]:
    """imdb_chart ({chart, kind}) or imdb_list ({url, kind}) — GraphQL, then map
    tt-ids → TMDB concurrently (each mapping is one cached /find call)."""
    if not isinstance(ref, dict):
        ref = {"url": str(ref or "")}
    kind = "show" if ref.get("kind") == "show" else "movie"
    chart = str(ref.get("chart") or "")
    if chart:
        chart_type = _IMDB_CHARTS.get(chart)
        if not chart_type:
            return []
        key = ("imdb", chart_type, kind)
        pairs_fn = lambda: _imdb_pairs_chart(chart_type)   # noqa: E731
    else:
        import re
        m = re.search(r"(ls\d+)", str(ref.get("url") or ""))
        if not m:
            return []
        key = ("imdb", m.group(1), kind)
        pairs_fn = lambda: _imdb_pairs_list(m.group(1))    # noqa: E731

    def build():
        pairs = pairs_fn()
        if not pairs:
            logger.info("imdb source yielded no titles (schema change?): %s", key)
            return []
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            tmdb_ids = list(ex.map(lambda p: eng.tmdb_from_imdb(p[0], kind), pairs))
        raw = [{"tmdb_id": tid, "title": name, "year": year}
               for (tt, name, year), tid in zip(pairs, tmdb_ids, strict=False) if tid]
        return _dedup_normed(raw)

    return _community_cached(key, build)


# ── community list sources (Trakt / MDBList) ────────────────────────────────
# Both need a free user key (video Settings → Metadata): Trakt reads public
# lists with just an app Client ID; MDBList aggregates IMDb/Trakt lists (the
# route to a REAL IMDb Top 250 and award lists). Fetched directly (no engine
# worker — they're list readers, not matchers) with a module TTL cache.
_COMMUNITY_CACHE: Dict[tuple, tuple] = {}
_COMMUNITY_TTL = 1800


def _http_json(url, headers=None, params=None):
    import requests
    r = requests.get(url, headers=headers or {}, params=params or {}, timeout=20)
    r.raise_for_status()
    return r


def _user_slug(ref, *path_markers) -> tuple | None:
    """'user/slug' from a bare pair or a full list URL.
    trakt.tv/users/{user}/lists/{slug} · mdblist.com/lists/{user}/{slug}"""
    s = str(ref or "").strip().strip("/")
    if not s:
        return None
    parts = [p for p in s.replace("?", "/").split("/") if p]
    for marker in path_markers:
        if marker in parts:
            i = parts.index(marker)
            if len(parts) > i + 1:
                # users/{user}/lists/{slug} or lists/{user}/{slug}
                tail = [p for p in parts[i + 1:] if p != "lists"]
                if len(tail) >= 2:
                    return tail[0], tail[1]
    return (parts[0], parts[1]) if len(parts) == 2 and "." not in parts[0] else None


def _community_cached(key, build):
    import time
    hit = _COMMUNITY_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _COMMUNITY_TTL:
        return hit[1]
    items = build()
    _COMMUNITY_CACHE[key] = (time.monotonic(), items)
    return items


def _fetch_trakt(db, ref: Any) -> List[Dict[str, Any]]:
    key = db.get_setting("trakt_api_key") if db is not None else None
    if not key:
        logger.info("trakt list skipped — no Trakt Client ID configured")
        return []
    us = _user_slug(ref, "users")
    if not us:
        return []

    def build():
        headers = {"trakt-api-version": "2", "trakt-api-key": key,
                   "Content-Type": "application/json"}
        raw: list = []
        page, page_count = 1, 1
        while page <= min(page_count, 10):        # 10×200 = 2000 items, plenty
            r = _http_json(f"https://api.trakt.tv/users/{us[0]}/lists/{us[1]}/items",
                           headers=headers, params={"page": page, "limit": 200})
            try:
                page_count = int(r.headers.get("x-pagination-page-count") or 1)
            except (TypeError, ValueError):
                page_count = 1
            for it in r.json() or []:
                media = it.get("movie") or it.get("show") or {}
                tid = (media.get("ids") or {}).get("tmdb")
                if tid:
                    raw.append({"tmdb_id": tid, "title": media.get("title"),
                                "year": media.get("year")})
            page += 1
        return _dedup_normed(raw)

    return _community_cached(("trakt", us), build)


def _fetch_mdblist(db, ref: Any) -> List[Dict[str, Any]]:
    key = db.get_setting("mdblist_api_key") if db is not None else None
    if not key:
        logger.info("mdblist skipped — no MDBList API key configured")
        return []
    us = _user_slug(ref, "lists")
    if not us:
        return []

    def build():
        r = _http_json(f"https://api.mdblist.com/lists/{us[0]}/{us[1]}/items",
                       params={"apikey": key})
        data = r.json() or []
        if isinstance(data, dict):                # some responses wrap in {movies, shows}
            data = (data.get("movies") or []) + (data.get("shows") or [])
        raw = []
        for it in data:
            tid = it.get("id") or it.get("tmdbid") or it.get("tmdb_id")
            if tid:
                raw.append({"tmdb_id": tid, "title": it.get("title"),
                            "year": it.get("release_year") or it.get("year")})
        return _dedup_normed(raw)

    return _community_cached(("mdblist", us), build)


def _fetch_list(eng, ref: Any) -> List[Dict[str, Any]]:
    # Lists get a higher page cap (500 items) than charts — a public TMDB list
    # is a complete membership, not a ranking to sample.
    raw: list = []
    page, total = 1, 1
    while page <= min(total, 25):
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
        # Community sources first — they read their own keys, no engine needed.
        try:
            if source == "trakt_list":
                return _fetch_trakt(db, ref)
            if source == "mdblist_list":
                return _fetch_mdblist(db, ref)
        except Exception:   # noqa: BLE001 - membership is best-effort; owned still syncs
            logger.debug("list fetch failed for %s %r", source, ref, exc_info=True)
            return []
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
            if source in ("imdb_chart", "imdb_list"):
                return _fetch_imdb(eng, ref)
        except Exception:   # noqa: BLE001 - membership is best-effort; owned still syncs
            logger.debug("list fetch failed for %s %r", source, ref, exc_info=True)
            return []
        logger.debug("list source %r not fetchable; owned-only", source)
        return []

    return fetch


__all__ = ["build_list_fetcher", "chart_keys"]
