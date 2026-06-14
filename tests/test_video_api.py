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
    assert any(r.startswith("/api/video/poster/") for r in rules)
    assert "/api/video/enrichment/services" in rules
    assert "/api/video/enrichment/<service>/status" in rules
    assert "/api/video/enrichment/<service>/unmatched" in rules
    assert "/api/video/enrichment/config" in rules
    assert "/api/video/enrichment/<service>/test" in rules


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
            "tmdb_api_key": "", "tvdb_api_key": ""}
        client.post("/api/video/enrichment/config", json={"tmdb_api_key": "abc", "tvdb_api_key": "xyz"})
        assert client.get("/api/video/enrichment/config").get_json() == {
            "tmdb_api_key": "abc", "tvdb_api_key": "xyz"}
        assert db.get_setting("tmdb_api_key") == "abc"
    finally:
        videoapi._video_db = None


def test_video_api_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "api" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"
