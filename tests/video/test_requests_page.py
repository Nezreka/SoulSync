"""Video Requests page — the request lifecycle keeps telling the story.

Boulder's report: approved requests "just sit there" — the page had no tabs,
no availability state, and no history management. Pins here:
  * list rows carry ``in_library`` (approved → 'In library' when the title
    exists in movies/shows) + per-status ``counts`` for the tabs
  * approve surfaces a resolve failure instead of toasting success while the
    row stays pending (the "approved but still says Approve" state)
  * DELETE handles pending (withdraw) AND resolved (remove from history),
    member-scoped; DELETE /requests/resolved bulk-clears history
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


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
        g.is_admin = g.profile_id == 1 or request.headers.get("X-Test-Admin") == "1"

    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


def _file_request(c, profile, kind="movie", tmdb_id=438631, title="Dune"):
    r = c.post("/api/video/requests", json={"kind": kind, "tmdb_id": tmdb_id, "title": title},
               headers={"X-Test-Profile": str(profile)})
    assert r.status_code == 200 and r.get_json()["success"]
    return r.get_json()["id"]


def _seed_owned_movie(db, tmdb_id=438631):
    return db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021,
                                    "tmdb_id": tmdb_id,
                                    "file": {"relative_path": "/dune.mkv", "size_bytes": 5}})


# ── list: counts + in_library annotation ─────────────────────────────────────

def test_list_carries_counts_and_in_library(client):
    c, db = client
    rid = _file_request(c, 2, tmdb_id=438631)
    _file_request(c, 2, kind="show", tmdb_id=999, title="Severance")
    _seed_owned_movie(db, 438631)

    d = c.get("/api/video/requests").get_json()          # admin sees all
    assert d["success"] and len(d["requests"]) == 2
    assert d["counts"] == {"pending": 2, "approved": 0, "denied": 0}
    by_id = {r["id"]: r for r in d["requests"]}
    assert by_id[rid]["in_library"] is True              # movie exists in library
    show_row = [r for r in d["requests"] if r["kind"] == "show"][0]
    assert show_row["in_library"] is False

    # Member scoping: profile 3 sees nothing, counts are theirs alone.
    d3 = c.get("/api/video/requests", headers={"X-Test-Profile": "3"}).get_json()
    assert d3["requests"] == [] and d3["counts"]["pending"] == 0


# ── approve: acquisition + honest resolve ────────────────────────────────────

def test_approve_movie_lands_on_wishlist_and_flips_status(client):
    c, db = client
    rid = _file_request(c, 2)
    r = c.post(f"/api/video/requests/{rid}/approve")
    d = r.get_json()
    assert r.status_code == 200 and d["success"] and d["kind"] == "movie"
    assert db.get_video_request(rid)["status"] == "approved"
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT status FROM video_wishlist WHERE kind='movie' AND tmdb_id=438631").fetchone()
    finally:
        conn.close()
    assert row is not None

    # Second approve is a clean conflict, not a re-add.
    assert c.post(f"/api/video/requests/{rid}/approve").status_code == 409


def test_approve_show_follows_watchlist(client):
    c, db = client
    rid = _file_request(c, 2, kind="show", tmdb_id=555, title="Silo")
    assert c.post(f"/api/video/requests/{rid}/approve").get_json()["success"]
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT state FROM video_watchlist WHERE kind='show' AND tmdb_id=555").fetchone()
    finally:
        conn.close()
    assert row is not None and row["state"] == "follow"


def test_approve_surfaces_resolve_failure(client, monkeypatch):
    """If the status flip fails after the add, the API must say so — the old
    code returned success and the row kept showing Approve."""
    c, db = client
    rid = _file_request(c, 2)
    monkeypatch.setattr(type(db), "resolve_video_request",
                        lambda self, *a, **k: False)
    r = c.post(f"/api/video/requests/{rid}/approve")
    assert r.status_code == 500
    assert not r.get_json()["success"]


def test_approve_admin_only(client):
    c, _db = client
    rid = _file_request(c, 2)
    r = c.post(f"/api/video/requests/{rid}/approve", headers={"X-Test-Profile": "2"})
    assert r.status_code == 403


# ── delete: withdraw, history removal, bulk clear ────────────────────────────

def test_member_withdraws_own_pending_only(client):
    c, _db = client
    rid = _file_request(c, 2)
    other = _file_request(c, 3, tmdb_id=777, title="Heat")
    # 3 can't delete 2's request
    assert c.delete(f"/api/video/requests/{rid}", headers={"X-Test-Profile": "3"}).status_code == 404
    # 2 withdraws their own
    assert c.delete(f"/api/video/requests/{rid}", headers={"X-Test-Profile": "2"}).get_json()["success"]
    # admin can delete anyone's
    assert c.delete(f"/api/video/requests/{other}").get_json()["success"]


def test_member_removes_own_resolved_row(client):
    c, db = client
    rid = _file_request(c, 2)
    c.post(f"/api/video/requests/{rid}/deny", json={"response": "nope"})
    assert db.get_video_request(rid)["status"] == "denied"
    assert c.delete(f"/api/video/requests/{rid}", headers={"X-Test-Profile": "2"}).get_json()["success"]
    assert db.get_video_request(rid) is None


def test_clear_resolved_keeps_pending_and_acquisition(client):
    c, db = client
    a = _file_request(c, 2, tmdb_id=1, title="A")
    b = _file_request(c, 2, tmdb_id=2, title="B")
    p = _file_request(c, 2, tmdb_id=3, title="C")
    c.post(f"/api/video/requests/{a}/approve")
    c.post(f"/api/video/requests/{b}/deny", json={})
    d = c.delete("/api/video/requests/resolved").get_json()
    assert d["success"] and d["removed"] == 2
    assert db.get_video_request(p)["status"] == "pending"     # pending untouched
    conn = db._get_connection()
    try:                                                       # approval side effect stays
        row = conn.execute("SELECT 1 FROM video_wishlist WHERE kind='movie' AND tmdb_id=1").fetchone()
    finally:
        conn.close()
    assert row is not None


def test_clear_resolved_member_scoped(client):
    c, db = client
    mine = _file_request(c, 2, tmdb_id=1, title="A")
    theirs = _file_request(c, 3, tmdb_id=2, title="B")
    c.post(f"/api/video/requests/{mine}/deny", json={})
    c.post(f"/api/video/requests/{theirs}/deny", json={})
    d = c.delete("/api/video/requests/resolved", headers={"X-Test-Profile": "2"}).get_json()
    assert d["removed"] == 1
    assert db.get_video_request(mine) is None
    assert db.get_video_request(theirs)["status"] == "denied"
