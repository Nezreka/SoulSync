"""Seam tests for the isolated /api/video blueprint (experimental branch).

Verifies the blueprint builds with its route, the dashboard endpoint returns
real (zeroed) JSON against an empty video.db, and that the video API package
imports nothing from the music side.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask


def _make_client(tmp_path):
    # Inject a tmp-backed DB directly so the endpoint never falls back to the
    # real default path (no stray database/video_library.db in the repo).
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi


def test_blueprint_exposes_dashboard_route():
    from api.video import create_video_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/video/dashboard" in rules
    assert "/api/video/scan/request" in rules
    assert "/api/video/scan/status" in rules
    assert "/api/video/scan/stop" in rules
    assert "/api/video/library" in rules
    assert "/api/video/libraries" in rules
    assert "/api/video/server" in rules
    assert any(r.startswith("/api/video/poster/") for r in rules)
    assert "/api/video/enrichment/services" in rules
    assert "/api/video/enrichment/<service>/status" in rules
    assert "/api/video/enrichment/<service>/unmatched" in rules
    assert "/api/video/enrichment/config" in rules
    assert "/api/video/enrichment/<service>/test" in rules
    assert "/api/video/detail/show/<int:show_id>" in rules
    assert "/api/video/detail/movie/<int:movie_id>" in rules
    assert "/api/video/detail/show/<int:show_id>/refresh-art" in rules
    assert "/api/video/detail/movie/<int:movie_id>/refresh-art" in rules
    assert "/api/video/detail/<kind>/<int:item_id>/extras" in rules
    assert "/api/video/search" in rules
    assert "/api/video/trending" in rules
    assert "/api/video/tmdb/<kind>/<int:tmdb_id>" in rules
    assert "/api/video/tmdb/show/<int:tv_id>/season/<int:season_number>" in rules
    assert "/api/video/person/<int:tmdb_id>" in rules
    assert "/api/video/episode/<int:tmdb_id>/<int:season>/<int:episode>" in rules
    assert any(r.startswith("/api/video/backdrop/") for r in rules)
    assert "/api/video/img" in rules
    assert "/api/video/discover/hero" in rules
    assert "/api/video/discover/genres" in rules
    assert "/api/video/discover/taste" in rules
    assert "/api/video/discover/list" in rules
    assert "/api/video/discover/morelike" in rules
    assert "/api/video/discover/trailer" in rules


def test_discover_trailer_returns_key(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    import core.video.enrichment.engine as eng_mod

    class FakeEng:
        def trailer(self, kind, tmdb_id): return {"key": "abc123", "name": "Official"}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    assert client.get("/api/video/discover/trailer?kind=movie&tmdb_id=5").get_json()["trailer"]["key"] == "abc123"
    # a non-numeric id is rejected without touching the engine
    assert client.get("/api/video/discover/trailer?kind=movie").get_json() == {"trailer": None}


def test_discover_morelike_builds_seeded_rails(tmp_path, monkeypatch):
    client, vapi = _make_client(tmp_path)
    db = vapi._video_db
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 1, "file": {"relative_path": "a.mkv"}})
    import core.video.enrichment.engine as eng_mod

    class FakeEng:
        def recommendations(self, kind, tmdb_id, page=1):
            return [{"kind": "movie", "tmdb_id": 100 + i} for i in range(6)]
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    rails = client.get("/api/video/discover/morelike").get_json()["rails"]
    assert rails and rails[0]["title"] == "More like Dune"
    assert len(rails[0]["items"]) == 6


def test_discover_list_pages_concatenates_and_dedupes(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    import core.video.enrichment.engine as eng_mod

    class FakeEng:
        def discover_curated(self, key, page=1):
            # page 1 → ids 1,2 ; page 2 → ids 2,3 (overlap on 2)
            return ([{"kind": "movie", "tmdb_id": 1}, {"kind": "movie", "tmdb_id": 2}] if page == 1
                    else [{"kind": "movie", "tmdb_id": 2}, {"kind": "movie", "tmdb_id": 3}])
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    r = client.get("/api/video/discover/list?key=popular_movies&pages=2")
    assert [it["tmdb_id"] for it in r.get_json()["items"]] == [1, 2, 3]   # concatenated + deduped


def test_discover_list_trending_fetches_once_despite_pages(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    import core.video.enrichment.engine as eng_mod
    calls = {"n": 0}

    class FakeEng:
        def trending(self):
            calls["n"] += 1
            return [{"kind": "movie", "tmdb_id": 9}]
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    r = client.get("/api/video/discover/list?key=trending&pages=3")
    assert calls["n"] == 1                                    # fixed list — not refetched per page
    assert [it["tmdb_id"] for it in r.get_json()["items"]] == [9]


def test_img_proxy_rejects_non_tmdb(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.get("/api/video/img?u=https://evil.example.com/x.jpg").status_code == 404
    assert client.get("/api/video/img").status_code == 404


def test_search_endpoint_empty_query(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.get("/api/video/search?q=")
    assert resp.status_code == 200
    assert resp.get_json() == {"results": [], "query": ""}


def test_search_endpoint_uses_engine(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)

    class FakeEngine:
        def search(self, q): return [{"kind": "movie", "tmdb_id": 1, "title": "Dune", "library_id": None}]
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    body = client.get("/api/video/search?q=dune").get_json()
    assert body["query"] == "dune" and body["results"][0]["title"] == "Dune"


def test_tmdb_detail_endpoint(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)

    class FakeEngine:
        def tmdb_detail(self, kind, tid): return {"source": "tmdb", "kind": kind, "id": tid, "title": "X"}
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    resp = client.get("/api/video/tmdb/movie/438631")
    assert resp.status_code == 200 and resp.get_json()["source"] == "tmdb"
    assert client.get("/api/video/tmdb/bogus/1").status_code == 400


def test_omdb_key_change_retries_unrated(tmp_path, monkeypatch):
    client, videoapi = _make_client(tmp_path)
    db = videoapi._video_db
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})
    db.apply_ratings("movie", mid, {})              # burned: synced, but no rating
    assert db.ratings_next() is None                # not pending
    monkeypatch.setattr("core.video.enrichment.engine.rebuild_video_enrichment_engine", lambda: None)
    resp = client.post("/api/video/enrichment/config", json={"omdb_api_key": "NEWKEY"})
    assert resp.status_code == 200
    assert db.ratings_next() is not None            # new key → re-queued for rating


def test_show_detail_endpoint(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        sid = videoapi._video_db.upsert_show_tree("plex", {
            "server_id": "s1", "title": "Show", "seasons": [
                {"season_number": 1, "episodes": [
                    {"episode_number": 1, "title": "Pilot",
                     "file": {"relative_path": "e1.mkv", "size_bytes": 5}}]}]})
        resp = client.get("/api/video/detail/show/%d" % sid)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["kind"] == "show" and d["episode_total"] == 1 and d["episode_owned"] == 1
        assert d["seasons"][0]["episodes"][0]["title"] == "Pilot"
        assert client.get("/api/video/detail/show/999999").status_code == 404
    finally:
        videoapi._video_db = None


def test_enrichment_priority_endpoint(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        assert client.get("/api/video/enrichment/priority").get_json()["priority"] == ""
        r = client.post("/api/video/enrichment/priority", json={"priority": "show"})
        assert r.status_code == 200 and r.get_json()["priority"] == "show"
        assert client.get("/api/video/enrichment/priority").get_json()["priority"] == "show"
        assert client.post("/api/video/enrichment/priority", json={"priority": "bogus"}).status_code == 400
    finally:
        videoapi._video_db = None


def test_monitor_toggle_endpoint(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        sid = videoapi._video_db.upsert_show_tree("plex", {"server_id": "s1", "title": "S"})
        r = client.post("/api/video/monitor", json={"kind": "show", "id": sid, "monitored": False})
        assert r.status_code == 200 and r.get_json()["monitored"] is False
        assert videoapi._video_db.show_detail(sid)["monitored"] is False
        r2 = client.post("/api/video/monitor", json={"kind": "show", "id": sid, "monitored": True})
        assert r2.status_code == 200 and videoapi._video_db.show_detail(sid)["monitored"] is True
        # bad inputs
        assert client.post("/api/video/monitor", json={"kind": "bogus", "id": sid}).status_code == 400
        assert client.post("/api/video/monitor", json={"kind": "show", "id": 999999, "monitored": True}).status_code == 404
    finally:
        videoapi._video_db = None


def test_movie_detail_endpoint(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        mid = videoapi._video_db.upsert_movie("plex", {"server_id": "m1", "title": "Dune"})
        resp = client.get("/api/video/detail/movie/%d" % mid)
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Dune"
        assert client.get("/api/video/detail/movie/999999").status_code == 404
    finally:
        videoapi._video_db = None


def test_dashboard_endpoint_returns_zeroed_json(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        resp = client.get("/api/video/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["library"]["movies"] == 0
        assert data["downloads"]["active"] == 0
        assert data["watchlist"] == 0 and data["wishlist"] == 0
    finally:
        videoapi._video_db = None  # don't leak the tmp DB to other tests


def test_library_endpoint_lists_content(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        videoapi._video_db.upsert_movie("plex", {"server_id": "m1", "title": "A",
                                                 "poster_url": "/library/metadata/1/thumb/9"})
        resp = client.get("/api/video/library?kind=movies")
        assert resp.status_code == 200
        data = resp.get_json()
        assert [m["title"] for m in data["items"]] == ["A"]
        assert data["items"][0]["has_poster"] is True           # flag, not the raw path
        assert "poster_url" not in data["items"][0]             # don't leak server paths
        assert data["pagination"]["total_count"] == 1
    finally:
        videoapi._video_db = None


def test_libraries_endpoint_lists_and_saves(tmp_path, monkeypatch):
    client, videoapi = _make_client(tmp_path)
    try:
        import core.video.sources as vs
        monkeypatch.setattr(vs, "list_video_libraries", lambda: {
            "server": "plex", "movies": [{"title": "Movies"}], "tv": [{"title": "TV"}]})
        import config.settings as cs
        monkeypatch.setattr(cs.config_manager, "get_active_media_server", lambda: "plex")

        data = client.get("/api/video/libraries").get_json()
        assert data["server"] == "plex"
        assert [m["title"] for m in data["movies"]] == ["Movies"]
        assert data["selected"]["movies"] is None

        assert client.post("/api/video/libraries", json={"movies": "Movies", "tv": "TV"}).status_code == 200
        data2 = client.get("/api/video/libraries").get_json()
        assert data2["selected"] == {"movies": "Movies", "tv": "TV"}
    finally:
        videoapi._video_db = None


def test_enrichment_endpoints(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    import core.video.enrichment.engine as eng_mod
    from core.video.enrichment.engine import VideoEnrichmentEngine

    class FakeClient:
        enabled = True
        def match(self, *a, **k): return None
        def test(self): return (True, "ok")

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    eng_mod._engine = VideoEnrichmentEngine(db, {"tmdb": FakeClient(), "tvdb": FakeClient()})
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        svc = client.get("/api/video/enrichment/services").get_json()
        assert {s["id"] for s in svc["services"]} == {"tmdb", "tvdb"}

        mid = db.upsert_movie("plex", {"server_id": "m1", "title": "X"})
        st = client.get("/api/video/enrichment/tmdb/status").get_json()
        assert st["enabled"] is True and st["stats"]["pending"] == 1

        db.enrichment_apply("tmdb", "movie", mid, matched=False)
        bd = client.get("/api/video/enrichment/tmdb/breakdown").get_json()
        assert bd["breakdown"]["movie"]["not_found"] == 1
        un = client.get("/api/video/enrichment/tmdb/unmatched?kind=movie&status=not_found").get_json()
        assert un["total"] == 1 and un["kind"] == "movie"

        assert client.post("/api/video/enrichment/tmdb/pause").get_json()["status"] == "paused"
        assert client.post("/api/video/enrichment/tmdb/resume").get_json()["status"] == "running"
        assert client.post("/api/video/enrichment/tmdb/retry",
                           json={"kind": "movie", "scope": "failed"}).get_json()["reset"] == 1
        assert client.post("/api/video/enrichment/tmdb/test").get_json()["success"] is True
        assert client.post("/api/video/enrichment/nope/test").status_code == 404
        assert client.get("/api/video/enrichment/nope/status").status_code == 404
    finally:
        videoapi._video_db = None
        eng_mod._engine = None


def test_enrichment_config_save_load(tmp_path, monkeypatch):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    import core.video.enrichment.engine as eng_mod
    # Don't build a real engine (would open the default-path DB + start threads).
    monkeypatch.setattr(eng_mod, "rebuild_video_enrichment_engine", lambda: None)

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        assert client.get("/api/video/enrichment/config").get_json() == {
            "tmdb_api_key": "", "tvdb_api_key": "", "omdb_api_key": "",
            "billboard_autoplay": True, "watch_region": "US"}
        client.post("/api/video/enrichment/config",
                    json={"tmdb_api_key": "abc", "tvdb_api_key": "xyz", "omdb_api_key": "om",
                          "billboard_autoplay": False, "watch_region": "gb"})
        assert client.get("/api/video/enrichment/config").get_json() == {
            "tmdb_api_key": "abc", "tvdb_api_key": "xyz", "omdb_api_key": "om",
            "billboard_autoplay": False, "watch_region": "GB"}
        assert db.get_setting("tmdb_api_key") == "abc" and db.get_setting("omdb_api_key") == "om"
        assert client.get("/api/video/prefs").get_json() == {
            "billboard_autoplay": False, "watch_region": "GB"}
    finally:
        videoapi._video_db = None


def test_video_api_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "api" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"


# ── Watchlist endpoints (shows + people) ────────────────────────────────

def test_watchlist_routes_registered():
    from api.video import create_video_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ("/api/video/watchlist", "/api/video/watchlist/add",
              "/api/video/watchlist/remove", "/api/video/watchlist/check",
              "/api/video/watchlist/counts"):
        assert r in rules, r


def test_watchlist_add_check_list_remove_roundtrip(tmp_path):
    client, _ = _make_client(tmp_path)

    # empty to start
    assert client.get("/api/video/watchlist").get_json() == {
        "success": True, "shows": [], "people": [],
        "counts": {"show": 0, "person": 0, "total": 0}}

    # add a show + a person
    r = client.post("/api/video/watchlist/add", json={
        "kind": "show", "tmdb_id": 1399, "title": "Game of Thrones",
        "poster_url": "/p.jpg", "library_id": 7})
    assert r.get_json() == {"success": True, "watched": True}
    client.post("/api/video/watchlist/add", json={
        "kind": "person", "tmdb_id": 287, "title": "Brad Pitt"})

    # list groups by kind
    data = client.get("/api/video/watchlist").get_json()
    assert data["counts"] == {"show": 1, "person": 1, "total": 2}
    assert data["shows"][0]["title"] == "Game of Thrones"
    assert data["people"][0]["tmdb_id"] == 287

    # check (hydration) — only watched ids come back, keys are strings
    chk = client.post("/api/video/watchlist/check",
                      json={"kind": "show", "tmdb_ids": [1399, 9999]}).get_json()
    assert chk == {"success": True, "results": {"1399": True}}

    # counts endpoint
    assert client.get("/api/video/watchlist/counts").get_json() == {
        "success": True, "show": 1, "person": 1, "total": 2}

    # remove
    rem = client.post("/api/video/watchlist/remove",
                      json={"kind": "show", "tmdb_id": 1399}).get_json()
    assert rem["success"] is True and rem["watched"] is False and rem["removed"] is True
    assert client.get("/api/video/watchlist/counts").get_json()["show"] == 0


def test_watchlist_add_validates_input(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/api/video/watchlist/add", json={"kind": "movie", "tmdb_id": 1, "title": "x"}).status_code == 400
    assert client.post("/api/video/watchlist/add", json={"kind": "show", "title": "no id"}).status_code == 400
    assert client.post("/api/video/watchlist/add", json={"kind": "show", "tmdb_id": 1}).status_code == 400  # no title
    assert client.post("/api/video/watchlist/remove", json={"kind": "person"}).status_code == 400
    assert client.post("/api/video/watchlist/check", json={"tmdb_ids": [1]}).status_code == 400  # no kind


def test_watchlist_endpoint_paginates_and_searches(tmp_path):
    client, _ = _make_client(tmp_path)
    for i in range(1, 6):
        client.post("/api/video/watchlist/add", json={"kind": "person", "tmdb_id": 300 + i, "title": "P%d" % i})
    d = client.get("/api/video/watchlist?kind=person&page=1&limit=2").get_json()
    assert d["success"] and len(d["items"]) == 2
    assert d["pagination"]["total_count"] == 5 and d["pagination"]["total_pages"] == 3
    assert d["counts"]["person"] == 5
    s = client.get("/api/video/watchlist?kind=person&search=P3").get_json()
    assert len(s["items"]) == 1 and s["items"][0]["title"] == "P3"


# ── wishlist endpoints ────────────────────────────────────────────────────────

def test_wishlist_add_movie_then_list(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "The Matrix", "year": 1999}})
    assert r.get_json() == {"success": True, "added": 1, "counts": {"movie": 1, "show": 0, "episode": 0, "total": 1}}
    lst = client.get("/api/video/wishlist?kind=movie").get_json()
    assert lst["success"] and lst["items"][0]["tmdb_id"] == 603 and lst["counts"]["movie"] == 1


def test_wishlist_add_episodes_groups_into_show(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/video/wishlist/add", json={
        "show": {"tmdb_id": 1396, "title": "Breaking Bad", "poster_url": "/bb.jpg"},
        "episodes": [{"season_number": 1, "episode_number": 1, "title": "Pilot"},
                     {"season_number": 1, "episode_number": 2}]})
    assert r.get_json()["added"] == 2
    show = client.get("/api/video/wishlist?kind=show").get_json()["items"][0]
    assert show["tmdb_id"] == 1396 and show["wanted"] == 2
    assert show["seasons"][0]["season_number"] == 1


def test_wishlist_add_requires_valid_body(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/api/video/wishlist/add", json={}).status_code == 400
    # show with no episodes is rejected (episodes are the atomic unit)
    assert client.post("/api/video/wishlist/add", json={"show": {"tmdb_id": 1, "title": "S"}}).status_code == 400


def test_wishlist_remove_scopes_via_api(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post("/api/video/wishlist/add", json={
        "show": {"tmdb_id": 1396, "title": "Breaking Bad"},
        "episodes": [{"season_number": 1, "episode_number": 1}, {"season_number": 1, "episode_number": 2}]})
    r = client.post("/api/video/wishlist/remove",
                    json={"scope": "episode", "tmdb_id": 1396, "season_number": 1, "episode_number": 2})
    assert r.get_json()["removed"] == 1 and r.get_json()["counts"]["episode"] == 1
    assert client.post("/api/video/wishlist/remove", json={"scope": "show", "tmdb_id": 1396}).get_json()["removed"] == 1
    assert client.post("/api/video/wishlist/remove", json={"scope": "bogus", "tmdb_id": 1}).status_code == 400


def test_wishlist_check_hydration(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "The Matrix"}})
    client.post("/api/video/wishlist/add", json={
        "show": {"tmdb_id": 1396, "title": "Breaking Bad"},
        "episodes": [{"season_number": 2, "episode_number": 3}]})
    res = client.post("/api/video/wishlist/check", json={"movie_ids": [603, 700], "show_tmdb_id": 1396}).get_json()
    assert res["movies"] == [603] and res["episodes"] == ["2_3"]


def test_wishlist_routes_registered():
    from flask import Flask
    from api.video import create_video_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ("/api/video/wishlist", "/api/video/wishlist/add", "/api/video/wishlist/remove",
              "/api/video/wishlist/check", "/api/video/wishlist/counts"):
        assert r in rules


def test_wishlist_check_by_show(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post("/api/video/wishlist/add", json={
        "show": {"tmdb_id": 1396, "title": "BB"}, "episodes": [{"season_number": 1, "episode_number": 1}]})
    res = client.post("/api/video/wishlist/check", json={"shows": [1396, 1399]}).get_json()
    assert res["by_show"]["1396"] == ["1_1"] and "1399" not in res["by_show"]


def test_wishlist_backfill_art_endpoint(tmp_path, monkeypatch):
    client, vapi = _make_client(tmp_path)
    db = vapi._video_db
    db.add_episodes_to_wishlist(1396, "BB", [{"season_number": 1, "episode_number": 1}])   # no art
    import core.video.enrichment.engine as eng_mod

    class FakeEng:
        def tmdb_season(self, tv, sn):
            return {"poster_url": "/s1.jpg", "episodes": [{"episode_number": 1, "still_url": "/s.jpg"}]}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    assert client.post("/api/video/wishlist/backfill-art").get_json()["success"] is True
    season = db.query_wishlist("show")["items"][0]["seasons"][0]
    assert season["poster_url"] == "/s1.jpg" and season["episodes"][0]["still_url"] == "/s.jpg"
