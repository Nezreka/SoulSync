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
    assert "/api/video/library" in rules


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
        videoapi._video_db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
        resp = client.get("/api/video/library")
        assert resp.status_code == 200
        data = resp.get_json()
        assert [m["title"] for m in data["movies"]] == ["A"]
        assert data["shows"] == []
    finally:
        videoapi._video_db = None


def test_video_api_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "api" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"
