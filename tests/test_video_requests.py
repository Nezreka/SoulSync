"""Video requests — the in-app Overseerr (arr-parity P4).

Members (no download rights) file requests; admins approve → the title enters
acquisition (movie → wishlist, show → watchlist + P2 monitor-policy expansion)
or deny with a note. Permission edges ride the blueprint's g context.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_REQ_JS = (_ROOT / "webui" / "static" / "video" / "video-requests.js").read_text(encoding="utf-8")
_DETAIL_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SIDE_JS = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    persona = {"profile_id": 1, "is_admin": True, "can_download": True, "profile_name": "Admin"}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)

    try:
        yield app.test_client(), db, persona
    finally:
        videoapi._video_db = None


def _as_member(persona, pid=5, name="Kid"):
    persona.update({"profile_id": pid, "is_admin": False, "can_download": False,
                    "profile_name": name})


def test_member_files_and_sees_only_their_own(app_db):
    client, db, persona = app_db
    _as_member(persona)
    out = client.post("/api/video/requests",
                      json={"kind": "movie", "tmdb_id": 603, "title": "The Matrix",
                            "year": 1999, "note": "please!"}).get_json()
    assert out["success"] and not out["already"]
    # idempotent while pending
    again = client.post("/api/video/requests",
                        json={"kind": "movie", "tmdb_id": 603, "title": "The Matrix"}).get_json()
    assert again["already"] is True
    # a different member sees nothing
    _as_member(persona, pid=6, name="Other")
    assert client.get("/api/video/requests").get_json()["requests"] == []
    # the admin sees it, with the requester's name
    persona.update({"profile_id": 1, "is_admin": True})
    rows = client.get("/api/video/requests").get_json()["requests"]
    assert len(rows) == 1 and rows[0]["requester_name"] == "Kid" and rows[0]["note"] == "please!"


def test_approve_movie_lands_on_the_wishlist(app_db):
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests",
                      json={"kind": "movie", "tmdb_id": 603, "title": "The Matrix",
                            "year": 1999}).get_json()["id"]
    # member cannot approve
    assert client.post("/api/video/requests/%d/approve" % rid).status_code == 403
    persona.update({"profile_id": 1, "is_admin": True})
    out = client.post("/api/video/requests/%d/approve" % rid,
                      json={"response": "enjoy"}).get_json()
    assert out["success"] is True
    assert db.wishlist_counts().get("movie") == 1
    req = db.get_video_request(rid)
    assert req["status"] == "approved" and req["admin_response"] == "enjoy"
    # terminal — a second approve 409s
    assert client.post("/api/video/requests/%d/approve" % rid).status_code == 409


def test_approve_show_expands_the_monitor_policy(app_db, monkeypatch):
    client, db, persona = app_db

    class _Eng:
        def tmdb_detail(self, kind, tid):
            return {"seasons": [{"season_number": 1}]}

        def tmdb_season(self, tid, sn):
            return {"episodes": [{"episode_number": 1, "title": "Pilot", "air_date": "2025-01-01"}]}

    import core.video.enrichment.engine as eng_mod
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    _as_member(persona)
    rid = client.post("/api/video/requests",
                      json={"kind": "show", "tmdb_id": 95396, "title": "Severance",
                            "monitor": "all"}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    out = client.post("/api/video/requests/%d/approve" % rid).get_json()
    assert out["success"] and out["wished"] == 1
    assert db.wishlist_counts().get("episode") == 1


def test_deny_and_withdraw_edges(app_db):
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests",
                      json={"kind": "movie", "tmdb_id": 1, "title": "A"}).get_json()["id"]
    assert client.post("/api/video/requests/%d/deny" % rid).status_code == 403   # member
    # withdraw own pending
    assert client.delete("/api/video/requests/%d" % rid).get_json()["success"]
    # gone → deny 404s even for admin
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.post("/api/video/requests/%d/deny" % rid).status_code == 404
    # deny flow with a note
    _as_member(persona)
    rid2 = client.post("/api/video/requests",
                       json={"kind": "movie", "tmdb_id": 2, "title": "B"}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.post("/api/video/requests/%d/deny" % rid2,
                       json={"response": "we have it at home"}).get_json()["success"]
    assert db.get_video_request(rid2)["admin_response"] == "we have it at home"


def test_counts_scope_admin_vs_member(app_db):
    client, db, persona = app_db
    _as_member(persona, pid=5)
    client.post("/api/video/requests", json={"kind": "movie", "tmdb_id": 1, "title": "A"})
    _as_member(persona, pid=6)
    client.post("/api/video/requests", json={"kind": "movie", "tmdb_id": 2, "title": "B"})
    assert client.get("/api/video/requests/counts").get_json()["pending"] == 1   # own only
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.get("/api/video/requests/counts").get_json()["pending"] == 2   # all


# ---------------------------------------------------------------------------
# Frontend contracts
# ---------------------------------------------------------------------------

def test_page_nav_and_module_exist():
    assert 'data-video-page="video-requests"' in _INDEX
    assert 'data-video-subpage="video-requests"' in _INDEX
    assert "data-video-requests-badge" in _INDEX
    assert "video-requests.js" in _INDEX
    assert "{ id: 'video-requests', label: 'Requests' }" in _SIDE_JS
    assert "'/api/video/requests/'" in _REQ_JS or "/api/video/requests/" in _REQ_JS


def test_no_download_profiles_get_the_request_button():
    assert 'data-vd-act="request"' in _DETAIL_JS
    assert "canDownload()" in _DETAIL_JS
    assert "sendRequest(act)" in _DETAIL_JS
    # the gated Get/Watchlist CTAs hide for those profiles
    assert "window.VideoGet && _canDl" in _DETAIL_JS
