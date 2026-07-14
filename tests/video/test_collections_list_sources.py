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


# ── IMDb (keyless — its public GraphQL endpoint, like current Kometa) ────────
def _gql_title(tt, name, year):
    return {"id": tt, "titleText": {"text": name}, "releaseYear": {"year": year}}


class _ImdbEngine:
    def tmdb_from_imdb(self, tt, kind):
        return {"tt0111161": 278, "tt0068646": 238}.get(tt)

    def imdb_poster(self, tt, kind):
        return f"https://img.tmdb/{tt}.jpg" if self.tmdb_from_imdb(tt, kind) else None


def test_imdb_chart_via_graphql(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    queries = []

    def gql(query):
        queries.append(query)
        return {"data": {"chartTitles": {"edges": [
            {"node": _gql_title("tt0111161", "The Shawshank Redemption", 1994)},
            {"node": _gql_title("tt0068646", "The Godfather", 1972)},
            {"node": _gql_title("tt0111161", "Dup", 1994)},
            {"node": _gql_title("tt9999999", "Unmappable", 2000)},
        ]}}}
    monkeypatch.setattr(ls, "_imdb_graphql", gql)

    fetch = ls.build_list_fetcher(engine_factory=lambda: _ImdbEngine())
    out = fetch("imdb_chart", {"chart": "top", "kind": "movie"})
    assert [(o["tmdb_id"], o["title"], o["year"]) for o in out] == \
        [(278, "The Shawshank Redemption", 1994), (238, "The Godfather", 1972)]
    # Art rides along — IMDb carries none, TMDB's /find record fills it.
    assert out[0]["poster_url"] == "https://img.tmdb/tt0111161.jpg"
    assert "TOP_RATED_MOVIES" in queries[0]
    assert fetch("imdb_chart", {"chart": "nope"}) == []


def test_imdb_list_paginates_cursors(monkeypatch):
    import core.video.collections.list_sources as ls
    monkeypatch.setattr(ls, "_COMMUNITY_CACHE", {})
    pages = []

    def gql(query):
        pages.append(query)
        if 'after: "c1"' in query:
            return {"data": {"list": {"titleListItemSearch": {
                "edges": [{"title": _gql_title("tt0068646", "The Godfather", 1972)}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        return {"data": {"list": {"titleListItemSearch": {
            "edges": [{"title": _gql_title("tt0111161", "Shawshank", 1994)}],
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}}
    monkeypatch.setattr(ls, "_imdb_graphql", gql)

    fetch = ls.build_list_fetcher(engine_factory=lambda: _ImdbEngine())
    out = fetch("imdb_list", {"url": "https://www.imdb.com/list/ls055592025/?ref=x", "kind": "movie"})
    assert [o["tmdb_id"] for o in out] == [278, 238]
    assert len(pages) == 2 and 'ls055592025' in pages[0]
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


# ── tt→TMDB mapping persists (an IMDb chart costs its lookups once EVER) ─────
def test_imdb_map_persists_across_engine_restarts(tmp_path):
    from types import SimpleNamespace
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    calls = []

    class _Client:
        def find_by_imdb(self, tt):
            calls.append(tt)
            return {"movie": 278, "show": None,
                    "movie_poster": "https://img.tmdb/278.jpg", "show_poster": None}

    def eng():
        e = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
        e.db = db
        from core.video.enrichment.engine import TTLCache
        e._cache = TTLCache(maxsize=64, ttl=1800)
        e.workers = {"tmdb": SimpleNamespace(enabled=True, client=_Client())}
        return e

    e1 = eng()
    assert e1.tmdb_from_imdb("tt0111161", "movie") == 278
    assert e1.imdb_poster("tt0111161", "movie") == "https://img.tmdb/278.jpg"
    assert calls == ["tt0111161"]

    # Fresh engine (restart): the persisted map answers — no network at all.
    e2 = eng()
    assert e2.tmdb_from_imdb("tt0111161", "movie") == 278
    assert e2.imdb_poster("tt0111161", "movie") == "https://img.tmdb/278.jpg"
    assert calls == ["tt0111161"]

    # A tt the LIBRARY already knows maps with zero network too.
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, imdb_id, title, has_file) "
                 "VALUES (9, 'plex', 'm9', 999, 'tt7777777', 'Owned', 1)")
    conn.commit(); conn.close()
    assert eng().tmdb_from_imdb("tt7777777", "movie") == 999
    assert calls == ["tt0111161"]


def test_imdb_map_table_upgrades_from_id_only_shape(tmp_path):
    # Boulder's live DB materialized the table WITHOUT the poster columns
    # (CREATE TABLE IF NOT EXISTS never upgrades an existing shape) — the
    # column migration must repair it on the next boot.
    import sqlite3
    from database.video_database import VideoDatabase
    path = tmp_path / "video_library.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE imdb_tmdb_map (
        imdb_id TEXT PRIMARY KEY, movie_tmdb INTEGER, show_tmdb INTEGER,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    conn.execute("INSERT INTO imdb_tmdb_map (imdb_id, movie_tmdb) VALUES ('tt1', 11)")
    conn.commit()
    conn.close()

    db = VideoDatabase(database_path=str(path))          # boot → migrations run
    db.put_imdb_tmdb("tt2", 22, None, movie_poster="https://img/22.jpg")
    assert db.get_imdb_tmdb("tt2")["movie_poster"] == "https://img/22.jpg"
    old = db.get_imdb_tmdb("tt1")                        # pre-upgrade row intact
    assert old["movie"] == 11 and old["movie_poster"] is None
