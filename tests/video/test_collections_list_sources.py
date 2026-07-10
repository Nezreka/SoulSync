"""The list fetcher's remote sources: charts (curated/trending), keyword themes,
TMDB lists, franchise — all against a fake engine (no network)."""

from __future__ import annotations

from core.video.collections.list_sources import build_list_fetcher, chart_keys
from core.video.collections.resolver import resolve_collection


def _items(ids, **extra):
    return [dict({"tmdb_id": i, "title": f"T{i}", "year": 2000, "poster": f"p{i}"}, **extra)
            for i in ids]


class _FakeEngine:
    """Curated lists paged 20/page; keyword + discover + list surfaces recorded."""

    def __init__(self, curated_total=60):
        self.curated_total = curated_total
        self.calls = []

    def discover_curated(self, key, page=1):
        self.calls.append(("curated", key, page))
        start = (page - 1) * 20
        ids = range(start + 1, min(start + 20, self.curated_total) + 1)
        return _items(ids)

    def trending(self, window="week", kind=None):
        self.calls.append(("trending", window, kind))
        return _items(range(900, 910))

    def keyword_id(self, query):
        self.calls.append(("kwid", query))
        return 207317 if query == "christmas" else None

    def discover_filter(self, kind, **kw):
        self.calls.append(("discover", kind, kw.get("keywords"), kw.get("page")))
        page = kw.get("page") or 1
        return _items(range(page * 100, page * 100 + 20)) if page <= 2 else []

    def list_page(self, list_id, page=1):
        self.calls.append(("list", list_id, page))
        return (_items([1, 2, 3]), 2) if page == 1 else (_items([3, 4]), 2)

    def collection(self, cid):
        self.calls.append(("collection", cid))
        return _items([71, 72])


def _fetcher(eng=None):
    eng = eng or _FakeEngine()
    return build_list_fetcher(engine_factory=lambda: eng), eng


def test_chart_pages_until_limit_and_dedups():
    fetch, eng = _fetcher(_FakeEngine(curated_total=100))
    out = fetch("tmdb_chart", {"chart": "top_movies", "limit": 50})
    assert len(out) == 50
    assert [o["tmdb_id"] for o in out[:3]] == [1, 2, 3]
    assert out[0]["poster_url"] == "p1"                     # engine 'poster' normalized
    pages = sorted(c[2] for c in eng.calls if c[0] == "curated")
    assert pages == [1, 2, 3]      # only the pages the limit needs (fetched concurrently)


def test_chart_trending_and_unknown():
    fetch, eng = _fetcher()
    out = fetch("tmdb_chart", {"chart": "trending_shows", "limit": 5})
    assert len(out) == 5 and ("trending", "week", "show") in eng.calls
    assert fetch("tmdb_chart", {"chart": "nope"}) == []
    assert fetch("tmdb_chart", {}) == []


def test_keyword_resolves_name_then_discovers():
    fetch, eng = _fetcher()
    out = fetch("tmdb_keyword", {"kind": "movie", "query": "christmas", "limit": 30})
    assert len(out) == 30
    assert ("kwid", "christmas") in eng.calls
    assert ("discover", "movie", "207317", 1) in eng.calls
    # Unknown keyword → empty, not an error.
    assert fetch("tmdb_keyword", {"kind": "movie", "query": "zzz-no-such"}) == []
    assert fetch("tmdb_keyword", "not-a-dict") == []


def test_tmdb_list_pages_and_dedups():
    fetch, _ = _fetcher()
    out = fetch("tmdb_list", "8241") or []
    assert [o["tmdb_id"] for o in out] == [1, 2, 3, 4]      # page-2 dup of 3 dropped


def test_franchise_and_engineless():
    fetch, _ = _fetcher()
    assert [o["tmdb_id"] for o in fetch("franchise", 10)] == [71, 72]
    dead = build_list_fetcher(engine_factory=lambda: None)
    assert dead("tmdb_chart", {"chart": "top_movies"}) == []


def test_chart_keys_per_media():
    assert "top_movies" in chart_keys("movie") and "top_shows" not in chart_keys("movie")
    assert "on_the_air" in chart_keys("show")


# ── resolver: chart/keyword refs come from the definition body ───────────────
def test_resolver_builds_chart_and_keyword_refs():
    seen = []

    def fetch(source, ref):
        seen.append((source, ref))
        return _items([5])

    class _Db:
        def owned_by_tmdb_ids(self, mt, ids):
            return []

    d = {"media_type": "movie", "kind": "list",
         "definition": {"source": "tmdb_chart", "chart": "top_movies", "limit": 250}}
    res = resolve_collection(_Db(), d, list_fetcher=fetch)
    assert res.ok and len(res.missing) == 1
    assert seen[0] == ("tmdb_chart", {"source": "tmdb_chart", "chart": "top_movies", "limit": 250})

    d = {"media_type": "show", "kind": "list",
         "definition": {"source": "tmdb_keyword", "query": "christmas", "limit": 100}}
    res = resolve_collection(_Db(), d, list_fetcher=fetch)
    assert res.ok
    assert seen[1][0] == "tmdb_keyword" and seen[1][1]["kind"] == "show"

    # Missing config → clear per-source errors.
    bad = resolve_collection(_Db(), {"media_type": "movie", "kind": "list",
                                     "definition": {"source": "tmdb_chart"}}, list_fetcher=fetch)
    assert not bad.ok and "no chart" in bad.error
    bad = resolve_collection(_Db(), {"media_type": "movie", "kind": "list",
                                     "definition": {"source": "tmdb_keyword"}}, list_fetcher=fetch)
    assert not bad.ok and "no keyword" in bad.error
