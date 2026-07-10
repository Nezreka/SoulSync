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


def test_union_combines_franchises_and_keywords():
    fetch, eng = _fetcher()
    out = fetch("tmdb_union", {"kind": "movie", "limit": 200,
                               "collections": [10, 119], "keywords": ["christmas"]})
    ids = [o["tmdb_id"] for o in out]
    assert 71 in ids and 72 in ids                          # franchise members (both ids)
    assert 100 in ids                                       # keyword discover members
    assert len(ids) == len(set(ids))                        # unioned, deduped
    assert ("collection", 10) in eng.calls and ("collection", 119) in eng.calls
    assert ("kwid", "christmas") in eng.calls
    assert fetch("tmdb_union", "not-a-dict") == []


def test_resolver_union_ref_and_validation():
    seen = []

    def fetch(source, ref):
        seen.append((source, ref))
        return _items([5])

    class _Db:
        def owned_by_tmdb_ids(self, mt, ids):
            return []

    d = {"media_type": "movie", "kind": "list",
         "definition": {"source": "tmdb_union", "collections": [119], "limit": 200}}
    res = resolve_collection(_Db(), d, list_fetcher=fetch)
    assert res.ok and seen[0][0] == "tmdb_union" and seen[0][1]["kind"] == "movie"
    bad = resolve_collection(_Db(), {"media_type": "movie", "kind": "list",
                                     "definition": {"source": "tmdb_union"}}, list_fetcher=fetch)
    assert not bad.ok and "no franchises or keywords" in bad.error


def test_tmdb_list_pages_and_dedups():
    fetch, _ = _fetcher()
    out = fetch("tmdb_list", "8241") or []
    assert [o["tmdb_id"] for o in out] == [1, 2, 3, 4]      # page-2 dup of 3 dropped


def test_franchise_and_engineless():
    fetch, _ = _fetcher()
    assert [o["tmdb_id"] for o in fetch("franchise", 10)] == [71, 72]
    dead = build_list_fetcher(engine_factory=lambda: None)
    assert dead("tmdb_chart", {"chart": "top_movies"}) == []


# ── IMDb (keyless scrape, the Kometa trick) ──────────────────────────────────
_IMDB_HTML = """<html><head>
<script type="application/ld+json">
{"@type":"ItemList","itemListElement":[
 {"item":{"url":"https://www.imdb.com/title/tt0111161/","name":"The Shawshank Redemption"}},
 {"item":{"url":"https://www.imdb.com/title/tt0068646/","name":"The Godfather"}},
 {"item":{"url":"https://www.imdb.com/title/tt0111161/","name":"Dup"}},
 {"item":{"url":"https://www.imdb.com/title/tt9999999/","name":"Unmappable"}}]}
</script></head><body></body></html>"""


class _ImdbEngine:
    def tmdb_from_imdb(self, tt, kind):
        return {"tt0111161": 278, "tt0068646": 238}.get(tt)


def test_imdb_chart_scrapes_and_maps(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    fetched = []
    monkeypatch.setattr(ls, "_http_text", lambda url: fetched.append(url) or _IMDB_HTML)

    fetch = ls.build_list_fetcher(engine_factory=lambda: _ImdbEngine())
    out = fetch("imdb_chart", {"chart": "top", "kind": "movie"})
    assert [(o["tmdb_id"], o["title"]) for o in out] == \
        [(278, "The Shawshank Redemption"), (238, "The Godfather")]   # deduped, unmappable dropped
    assert fetched == ["https://www.imdb.com/chart/top/"]
    assert fetch("imdb_chart", {"chart": "nope"}) == []


def test_imdb_list_url_and_fallback_regex(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    # No JSON-LD (markup change) → regex sweep of /title/tt links still works.
    monkeypatch.setattr(ls, "_http_text",
                        lambda url: '<a href="/title/tt0111161/">x</a><a href="/title/tt0068646/">y</a>')
    fetch = ls.build_list_fetcher(engine_factory=lambda: _ImdbEngine())
    out = fetch("imdb_list", {"url": "https://www.imdb.com/list/ls055592025/?ref=x", "kind": "movie"})
    assert [o["tmdb_id"] for o in out] == [278, 238]
    assert fetch("imdb_list", {"url": "not-a-list"}) == []


def test_resolver_imdb_refs():
    from core.video.collections.resolver import resolve_collection
    seen = []

    def fetch(source, ref):
        seen.append((source, ref))
        return _items([5])

    class _Db:
        def owned_by_tmdb_ids(self, mt, ids):
            return []

    d = {"media_type": "show", "kind": "list",
         "definition": {"source": "imdb_chart", "chart": "toptv"}}
    assert resolve_collection(_Db(), d, list_fetcher=fetch).ok
    assert seen[0][0] == "imdb_chart" and seen[0][1]["kind"] == "show"
    bad = resolve_collection(_Db(), {"media_type": "movie", "kind": "list",
                                     "definition": {"source": "imdb_list"}}, list_fetcher=fetch)
    assert not bad.ok and "no list URL" in bad.error


# ── community sources (Trakt / MDBList) ──────────────────────────────────────
class _SettingsDb:
    def __init__(self, **settings):
        self._s = settings

    def get_setting(self, key, default=None):
        return self._s.get(key, default)


def _fake_http(responses):
    """responses: url-substring -> (json, headers). Records calls."""
    calls = []

    def http(url, headers=None, params=None):
        calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        for frag, (body, hdrs) in responses.items():
            if frag in url:
                class R:
                    def json(self):
                        return body
                r = R()
                r.headers = hdrs or {}
                return r
        raise AssertionError("unexpected url " + url)
    http.calls = calls
    return http


def test_trakt_list_fetch_paginates_and_maps_tmdb_ids(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    body = [
        {"type": "movie", "movie": {"title": "M1", "year": 2000, "ids": {"tmdb": 11}}},
        {"type": "show", "show": {"title": "S1", "year": 2010, "ids": {"tmdb": 22}}},
        {"type": "movie", "movie": {"title": "NoId", "ids": {}}},
    ]
    http = _fake_http({"api.trakt.tv/users/boulder/lists/faves/items":
                       (body, {"x-pagination-page-count": "1"})})
    monkeypatch.setattr(ls, "_http_json", http)

    fetch = ls.build_list_fetcher(_SettingsDb(trakt_api_key="cid123"),
                                  engine_factory=lambda: None)
    out = fetch("trakt_list", "https://trakt.tv/users/boulder/lists/faves")
    assert [(o["tmdb_id"], o["title"]) for o in out] == [(11, "M1"), (22, "S1")]
    assert http.calls[0]["headers"]["trakt-api-key"] == "cid123"
    # No key → clean empty (owned-only), not an error.
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    fetch2 = ls.build_list_fetcher(_SettingsDb(), engine_factory=lambda: None)
    assert fetch2("trakt_list", "boulder/faves") == []


def test_mdblist_fetch_and_user_slug_forms(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    body = [{"id": 278, "title": "Shawshank", "release_year": 1994, "mediatype": "movie"},
            {"id": None, "title": "junk"},
            {"tmdbid": 500, "title": "AltKey", "year": 2001}]
    http = _fake_http({"api.mdblist.com/lists/linas/imdb-top-250/items": (body, {})})
    monkeypatch.setattr(ls, "_http_json", http)

    fetch = ls.build_list_fetcher(_SettingsDb(mdblist_api_key="k1"),
                                  engine_factory=lambda: None)
    out = fetch("mdblist_list", "https://mdblist.com/lists/linas/imdb-top-250/")
    assert [(o["tmdb_id"], o["year"]) for o in out] == [(278, 1994), (500, 2001)]
    assert http.calls[0]["params"]["apikey"] == "k1"
    # bare user/slug form works too (cache returns without a second fetch)
    out2 = fetch("mdblist_list", "linas/imdb-top-250")
    assert len(out2) == 2 and len(http.calls) == 1


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
