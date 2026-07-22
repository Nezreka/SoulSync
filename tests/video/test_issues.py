"""Video Issues — the music standard: any profile reports + sees its own;
the admin sees all (with reporter names) and triages; lifecycle
open → in_progress → resolved | dismissed, reopenable; resolved retained;
snapshot_data captures the item's state at report time."""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_movie(db):
    return db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021,
                                    "tmdb_id": 438631, "genres": ["Sci-Fi"],
                                    "poster_url": "https://img/dune.jpg",
                                    "file": {"relative_path": "/dune.mkv", "size_bytes": 5,
                                             "resolution": "2160p"}})


# ── DB lifecycle ─────────────────────────────────────────────────────────────
def test_issue_crud_scoping_and_counts(db):
    a = db.create_issue(2, "movie", 1, "wrong_poster", "Bad art", reporter_name="Kid")
    b = db.create_issue(3, "movie", 1, "other", "Hm", priority="high")
    assert {i["id"] for i in db.get_issues(2, is_admin=False)} == {a}      # own only
    assert {i["id"] for i in db.get_issues(1, is_admin=True)} == {a, b}    # admin sees all
    # Ordering: open+high first.
    assert db.get_issues(1, is_admin=True)[0]["id"] == b
    assert db.update_issue(a, {"status": "resolved", "resolved_by": 1,
                               "resolved_at": "2026-07-10 00:00:00",
                               "admin_response": "fixed it"})
    got = db.get_issue(a)
    assert got["status"] == "resolved" and got["admin_response"] == "fixed it"
    assert db.update_issue(a, {"nonsense": 1}) is False                    # whitelist
    c = db.get_issue_counts(is_admin=True)
    assert c == {"open": 1, "in_progress": 0, "resolved": 1, "dismissed": 0, "total": 2}
    assert db.get_issue_counts(is_admin=False, profile_id=3)["open"] == 1
    assert db.delete_issue(b) and db.get_issue(b) is None
    assert db.get_issue(a)["status"] == "resolved"                         # retained


# ── API contract ─────────────────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.profile_name = "Tester %s" % g.profile_id
        g.can_download = True
        # Mirrors web_server: profile 1 is always admin; others per their flag
        # (X-Test-Admin simulates a secondary admin profile).
        g.is_admin = g.profile_id == 1 or request.headers.get("X-Test-Admin") == "1"

    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


def test_report_and_admin_triage_over_http(client):
    c, db = client
    mid = _seed_movie(db)
    # A non-admin profile reports (snapshot built server-side).
    r = c.post("/api/video/issues", headers={"X-Test-Profile": "2"},
               json={"entity_type": "movie", "entity_id": mid, "category": "wrong_poster",
                     "title": "Wrong poster: Dune", "description": "That's the 1984 one",
                     "priority": "high"})
    assert r.status_code == 201
    iid = r.get_json()["id"]

    # Reporter sees their own issue, without reporter_name (admin-only field).
    own = c.get("/api/video/issues", headers={"X-Test-Profile": "2"}).get_json()["issues"]
    assert len(own) == 1 and "reporter_name" not in own[0]
    snap = own[0]["snapshot_data"]
    assert snap["title"] == "Dune" and snap["year"] == 2021
    assert snap["poster"] == f"/api/video/poster/movie/{mid}"
    assert snap["files"][0]["resolution"] == "2160p"

    # Another profile sees nothing; the admin sees it with the reporter name.
    assert c.get("/api/video/issues", headers={"X-Test-Profile": "3"}).get_json()["issues"] == []
    admin = c.get("/api/video/issues").get_json()["issues"]
    assert admin[0]["reporter_name"] == "Tester 2"

    # Owner may edit title/description only; status change is refused.
    assert c.put(f"/api/video/issues/{iid}", headers={"X-Test-Profile": "2"},
                 json={"status": "resolved"}).status_code == 403
    assert c.put(f"/api/video/issues/{iid}", headers={"X-Test-Profile": "2"},
                 json={"title": "Wrong poster: Dune (2021)"}).get_json()["success"]

    # Admin resolves → stamps; reopen → clears.
    assert c.put(f"/api/video/issues/{iid}",
                 json={"status": "resolved", "admin_response": "Re-matched art"}).get_json()["success"]
    got = c.get(f"/api/video/issues/{iid}").get_json()["issue"]
    assert got["resolved_by"] == 1 and got["resolved_at"]
    assert c.put(f"/api/video/issues/{iid}", json={"status": "open"}).get_json()["success"]
    assert c.get(f"/api/video/issues/{iid}").get_json()["issue"]["resolved_at"] is None

    counts = c.get("/api/video/issues/counts").get_json()["counts"]
    assert counts["open"] == 1 and counts["total"] == 1

    # A stranger can't withdraw someone else's issue; the owner can.
    assert c.delete(f"/api/video/issues/{iid}",
                    headers={"X-Test-Profile": "3"}).status_code == 403
    assert c.delete(f"/api/video/issues/{iid}",
                    headers={"X-Test-Profile": "2"}).get_json()["success"]


def test_secondary_admin_and_strict_owner_edits(client):
    """A profile flagged is_admin (not profile 1) gets FULL admin powers — the
    frontend checks the same flag, so the two sides can't split-brain. And an
    owner's mixed payload (title + status) is an outright 403, never a silent
    partial apply (music rule)."""
    c, db = client
    mid = _seed_movie(db)
    iid = c.post("/api/video/issues", headers={"X-Test-Profile": "2"},
                 json={"entity_type": "movie", "entity_id": mid, "category": "other",
                       "title": "Hm"}).get_json()["id"]
    # Mixed owner payload → 403, and nothing applied.
    r = c.put(f"/api/video/issues/{iid}", headers={"X-Test-Profile": "2"},
              json={"title": "sneaky", "status": "resolved"})
    assert r.status_code == 403
    assert db.get_issue(iid)["title"] == "Hm"
    # Secondary admin (profile 7, is_admin) sees all + reporter name + resolves.
    adm = {"X-Test-Profile": "7", "X-Test-Admin": "1"}
    seen = c.get("/api/video/issues", headers=adm).get_json()["issues"]
    assert len(seen) == 1 and seen[0]["reporter_name"] == "Tester 2"
    assert c.put(f"/api/video/issues/{iid}", headers=adm,
                 json={"status": "resolved"}).get_json()["success"]
    assert db.get_issue(iid)["resolved_by"] == 7


def test_create_validation(client):
    c, db = client
    mid = _seed_movie(db)
    bad = [
        {"entity_type": "album", "entity_id": mid, "category": "other", "title": "x"},
        {"entity_type": "movie", "entity_id": mid, "category": "nope", "title": "x"},
        {"entity_type": "show", "entity_id": mid, "category": "bad_quality", "title": "x"},
        {"entity_type": "movie", "entity_id": mid, "category": "other", "title": ""},
    ]
    for payload in bad:
        assert c.post("/api/video/issues", json=payload).status_code == 400
    cats = c.get("/api/video/issues/categories").get_json()["categories"]
    assert any(x["key"] == "missing_content" and x["applies"] == ["show"] for x in cats)
