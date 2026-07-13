"""Seam tests for the Studios (production company) watchlist — Phase 1 data layer.

Covers the three tiers, each with a fake so no test hits TMDB:
  * TMDBClient.search_companies / company / company_movies — field mapping + URL building
  * VideoEnrichmentEngine.company_search (namesake ranking), company_detail (cache),
    company_movies (batched ownership annotation)
  * the /api/video/studio/* + studio-merged search endpoints
"""

from __future__ import annotations

import sys
import types

import pytest
from flask import Flask

from database.video_database import VideoDatabase
from core.video.enrichment.clients import TMDBClient
from core.video.enrichment.engine import VideoEnrichmentEngine


# --------------------------------------------------------------------------- #
# TMDBClient — the raw HTTP → dict mapping                                     #
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, body, status=200):
        self._b = body
        self.status_code = status

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


def _fake_requests(router):
    """A stand-in `requests` module whose .get routes (url → _Resp) via `router`,
    recording every (url, params) so tests can assert what was queried."""
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params or {}))
        return router(url, params or {})

    return types.SimpleNamespace(get=get), calls


def test_search_companies_maps_and_skips_idless(monkeypatch):
    fake, _ = _fake_requests(lambda url, p: _Resp({"results": [
        {"id": 41077, "name": "A24", "logo_path": "/a24.png", "origin_country": "US"},
        {"name": "No Id Co"},                                   # dropped — no id
        {"id": 2, "name": "Logoless", "logo_path": None},
    ]}))
    monkeypatch.setitem(sys.modules, "requests", fake)
    out = TMDBClient("KEY").search_companies("A24")
    assert [c["tmdb_id"] for c in out] == [41077, 2]            # id-less skipped
    assert out[0] == {"kind": "studio", "tmdb_id": 41077, "title": "A24",
                      "logo": "https://image.tmdb.org/t/p/w500/a24.png", "origin_country": "US"}
    assert out[1]["logo"] is None                              # no logo_path → None, not a broken URL


def test_search_companies_blank_query_skips_http(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: pytest.fail("hit HTTP")))
    assert TMDBClient("KEY").search_companies("   ") == []


def test_company_detail_maps_and_404_is_none(monkeypatch):
    def router(url, p):
        if url.endswith("/company/999"):
            return _Resp({"status_code": 34}, status=404)
        return _Resp({"id": 41077, "name": "A24", "description": "indie darling",
                      "logo_path": "/a24.png", "headquarters": "New York City, New York",
                      "origin_country": "US", "homepage": "https://a24films.com"})
    fake, _ = _fake_requests(router)
    monkeypatch.setitem(sys.modules, "requests", fake)
    c = TMDBClient("KEY")
    assert c.company(999) is None                              # 404 → None, no raise
    d = c.company(41077)
    assert d["tmdb_id"] == 41077 and d["headquarters"] == "New York City, New York"
    assert d["logo"] == "https://image.tmdb.org/t/p/w500/a24.png"
    assert d["homepage"] == "https://a24films.com"


def test_company_movies_builds_discover_query(monkeypatch):
    fake, calls = _fake_requests(lambda url, p: _Resp({
        "page": 1, "total_pages": 9, "total_results": 177,
        "results": [
            {"id": 5, "title": "Hereditary", "release_date": "2018-06-07",
             "vote_average": 7.3, "popularity": 41.0, "vote_count": 4000, "poster_path": "/h.jpg"},
            {"title": "no id"},                                # dropped
        ]}))
    monkeypatch.setitem(sys.modules, "requests", fake)
    out = TMDBClient("KEY").company_movies(41077, page=2, sort="popularity.desc")
    url, params = calls[0]
    assert url.endswith("/discover/movie")
    assert params["with_companies"] == "41077" and params["sort_by"] == "popularity.desc"
    assert params["page"] == 2
    assert out["total_results"] == 177 and out["total_pages"] == 9
    assert len(out["results"]) == 1                            # id-less movie skipped
    m = out["results"][0]
    assert (m["title"], m["year"], m["date"]) == ("Hereditary", "2018", "2018-06-07")
    assert m["poster"] == "https://image.tmdb.org/t/p/w300/h.jpg"


def test_company_movies_clamps_page(monkeypatch):
    fake, calls = _fake_requests(lambda url, p: _Resp({"results": []}))
    monkeypatch.setitem(sys.modules, "requests", fake)
    TMDBClient("KEY").company_movies(1, page=9999)             # TMDB caps discover at 500
    assert calls[0][1]["page"] == 500


# --------------------------------------------------------------------------- #
# VideoEnrichmentEngine — ranking, cache, ownership annotation                #
# --------------------------------------------------------------------------- #

class FakeTmdb:
    """A studio-aware fake client: canned companies + a movie count per company so
    the engine's namesake ranking has something to sort by."""
    enabled = True

    def __init__(self):
        self.detail_calls = 0
        self._companies = [
            {"kind": "studio", "tmdb_id": 293354, "title": "A24", "logo": "l", "origin_country": "GB"},
            {"kind": "studio", "tmdb_id": 41077, "title": "A24", "logo": "l", "origin_country": "US"},
        ]
        self._counts = {41077: 177, 293354: 1}                 # the real A24 has 177 films

    def search_companies(self, query):
        return list(self._companies)

    def company(self, company_id):
        self.detail_calls += 1
        return {"tmdb_id": company_id, "name": "A24", "headquarters": "New York City, New York"}

    def company_movies(self, company_id, *, page=1, sort="primary_release_date.desc"):
        n = self._counts.get(company_id, 0)
        results = [{"kind": "movie", "tmdb_id": 500 + i, "title": f"F{i}"} for i in range(min(n, 3))]
        return {"results": results, "page": page, "total_pages": 1, "total_results": n}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _engine(db):
    return VideoEnrichmentEngine(db, {"tmdb": FakeTmdb()})


def test_company_search_ranks_the_real_studio_first(db):
    # TMDB returns the 1-film namesake before the real 177-film A24; the engine must
    # attach movie_count and float the substantial studio to the top so the UI picks right.
    out = _engine(db).company_search("A24")
    assert [c["tmdb_id"] for c in out] == [41077, 293354]
    assert out[0]["movie_count"] == 177 and out[1]["movie_count"] == 1


def test_company_search_disabled_or_empty(db):
    class Off:
        enabled = False
        def search_companies(self, q): return []
    assert VideoEnrichmentEngine(db, {"tmdb": Off()}).company_search("A24") == []
    assert VideoEnrichmentEngine(db, {}).company_search("A24") == []   # no tmdb worker at all


def test_company_detail_is_cached(db):
    eng = _engine(db)
    client = eng.workers["tmdb"].client
    a = eng.company_detail(41077)
    b = eng.company_detail(41077)
    assert a["headquarters"] == "New York City, New York"
    assert b == a and client.detail_calls == 1                # second read served from cache


def test_company_movies_annotates_owned(db):
    # own one of the studio's films → that row carries its library id, the rest are None.
    mid = db.upsert_movie("plex", {"server_id": "s1", "title": "F1", "tmdb_id": 501})
    out = _engine(db).company_movies(41077)
    owned = {m["tmdb_id"]: m["library_id"] for m in out["results"]}
    assert owned[501] == mid
    assert owned[500] is None and owned[502] is None


def test_company_search_drops_fuzzy_noise_and_empty_shells(db):
    # TMDB /search/company is fuzzy: 'A24' also returns N24 / A2O and a 0-film 'A24 Music'.
    class Noisy:
        enabled = True
        def search_companies(self, q):
            return [{"kind": "studio", "tmdb_id": 41077, "title": "A24", "logo": "l"},
                    {"kind": "studio", "tmdb_id": 999, "title": "N24", "logo": "l"},   # name miss
                    {"kind": "studio", "tmdb_id": 888, "title": "A2O", "logo": "l"},   # name miss
                    {"kind": "studio", "tmdb_id": 777, "title": "A24 Music", "logo": None}]  # 0 films
        def company_movies(self, cid, *, page=1, sort="primary_release_date.desc"):
            counts = {41077: 177, 999: 23, 888: 1, 777: 0}
            return {"results": [], "total_pages": 1, "total_results": counts.get(cid, 0)}
    out = VideoEnrichmentEngine(db, {"tmdb": Noisy()}).company_search("A24")
    assert [c["tmdb_id"] for c in out] == [41077]   # only the real, non-empty A24 survives


# --------------------------------------------------------------------------- #
# API endpoints                                                               #
# --------------------------------------------------------------------------- #

def _make_studio_client(tmp_path):
    import api.video as videoapi
    import core.video.enrichment.engine as eng_mod
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    eng_mod._engine = _engine(db)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi, eng_mod, db


def test_studio_detail_endpoint(tmp_path):
    client, videoapi, eng_mod, _ = _make_studio_client(tmp_path)
    try:
        r = client.get("/api/video/studio/41077").get_json()
        assert r["success"] is True
        assert r["studio"]["headquarters"] == "New York City, New York"
        assert r["movies"]["total_results"] == 177
    finally:
        videoapi._video_db = None
        eng_mod._engine = None


def test_studio_movies_endpoint_rejects_evil_sort(tmp_path):
    client, videoapi, eng_mod, _ = _make_studio_client(tmp_path)
    try:
        # a sort not on the allowlist must not reach TMDB — it falls back to the default.
        r = client.get("/api/video/studio/41077/movies?sort=title;DROP").get_json()
        assert r["success"] is True and r["total_results"] == 177
    finally:
        videoapi._video_db = None
        eng_mod._engine = None


def test_studio_search_endpoint(tmp_path):
    # Studios have their OWN endpoint (kept out of the main search so it paints without
    # waiting on the per-studio film-count ranking).
    client, videoapi, eng_mod, _ = _make_studio_client(tmp_path)
    try:
        data = client.get("/api/video/search/studios?q=A24").get_json()
        kinds = {r.get("kind") for r in data["results"]}
        assert "studio" in kinds
        assert client.get("/api/video/search/studios").get_json() == {"results": [], "query": ""}
    finally:
        videoapi._video_db = None
        eng_mod._engine = None
