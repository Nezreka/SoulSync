"""video requests, sept 24 2026 review.

  * a show request defaulted to the 'future' policy, which on an ENDED show
    wishes nothing: approved, then "Acquiring…" forever
  * title/year/poster were whatever the member typed; tmdb_id "abc" was a 500
  * two members asking for the same title made two rows; approving one left
    the other pending forever, and nobody was ever told anything
  * approve added the title THEN flipped the row, so a deny landing between
    left a wishlisted title reading "denied"
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    import core.profile_notify as pn
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    notes = []
    monkeypatch.setattr(pn, "_journal", lambda pid, kind, msg: notes.append((pid, kind, msg)))
    monkeypatch.setattr(pn, "_emitter", None)
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.profile_name = "Tester %s" % g.profile_id
        g.can_download = True
        g.is_admin = g.profile_id == 1

    try:
        yield app.test_client(), videoapi._video_db, notes, vreq
    finally:
        videoapi._video_db = None


def _file(c, profile, **body):
    body = {"kind": "movie", "tmdb_id": 438631, "title": "Dune", **body}
    return c.post("/api/video/requests", json=body, headers={"X-Test-Profile": str(profile)})


def test_show_request_without_a_pick_asks_for_every_season(client):
    c, db, _notes, _v = client
    rid = _file(c, 2, kind="show", tmdb_id=1396, title="Breaking Bad").get_json()["id"]
    assert db.get_video_request(rid)["monitor"] == "all"
    rid2 = _file(c, 3, kind="show", tmdb_id=1396, title="Breaking Bad",
                 monitor="future").get_json()["id"]
    assert db.get_video_request(rid2)["monitor"] == "future"


def test_bad_tmdb_id_is_a_400_not_a_500(client):
    c, _db, _n, _v = client
    assert _file(c, 2, tmdb_id="abc").status_code == 400
    assert _file(c, 2, tmdb_id=-5).status_code == 400


def test_tmdb_metadata_wins_over_what_the_member_typed(client, monkeypatch):
    c, db, _n, vreq = client
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: {
        "title": "Dune", "year": 2021, "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg"})
    rid = _file(c, 2, title="free movies click here",
                poster_url="http://tracker.example/pixel.gif").get_json()["id"]
    row = db.get_video_request(rid)
    assert row["title"] == "Dune" and row["year"] == 2021
    assert row["poster_url"] == "https://image.tmdb.org/t/p/w500/x.jpg"


def test_an_untrusted_poster_is_dropped_when_tmdb_is_off(client):
    c, db, _n, _v = client
    rid = _file(c, 2, poster_url="http://tracker.example/pixel.gif").get_json()["id"]
    assert db.get_video_request(rid)["poster_url"] is None


def test_an_owned_movie_cannot_be_requested(client, monkeypatch):
    c, _db, _n, vreq = client
    monkeypatch.setattr(vreq, "_tmdb_lookup",
                        lambda kind, tid: {"redirect": {"source": "library", "kind": kind, "id": 4}})
    r = _file(c, 2)
    assert r.status_code == 409 and r.get_json()["in_library"] is True


def test_approving_one_ask_approves_everyone_who_asked(client):
    c, db, notes, _v = client
    a = _file(c, 2).get_json()["id"]
    b = _file(c, 3).get_json()["id"]
    r = c.post(f"/api/video/requests/{a}/approve")
    assert r.get_json()["approved"] == 2
    assert db.get_video_request(a)["status"] == db.get_video_request(b)["status"] == "approved"
    told = {pid for pid, kind, msg in notes if "approved" in msg}
    assert told == {2, 3}


def test_declining_tells_everyone_with_the_reason(client):
    c, db, notes, _v = client
    a = _file(c, 2).get_json()["id"]
    b = _file(c, 3).get_json()["id"]
    c.post(f"/api/video/requests/{b}/deny", json={"response": "already on netflix"})
    assert db.get_video_request(a)["status"] == "denied"
    assert {pid for pid, k, m in notes if "already on netflix" in m} == {2, 3}


def test_admin_can_change_the_seasons_on_approve(client, monkeypatch):
    c, db, _n, _v = client
    seen = {}
    import core.video.monitor_policy as mp
    monkeypatch.setattr(mp, "episodes_for_policy",
                        lambda engine, tid, policy, today: seen.setdefault("policy", policy) and [])
    rid = _file(c, 2, kind="show", tmdb_id=1396, title="Breaking Bad").get_json()["id"]
    c.post(f"/api/video/requests/{rid}/approve", json={"monitor": "first_season"})
    assert seen["policy"] == "first_season"


def test_arrival_is_stamped_and_told_once(client):
    c, db, notes, vreq = client
    rid = _file(c, 2).get_json()["id"]
    c.post(f"/api/video/requests/{rid}/approve")
    assert vreq.sweep_arrivals() == 0          # not in the library yet
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021, "tmdb_id": 438631,
                             "file": {"relative_path": "/dune.mkv", "size_bytes": 5}})
    assert vreq.sweep_arrivals() == 1
    assert vreq.sweep_arrivals() == 0          # once
    assert db.get_video_request(rid)["available_at"]
    assert [m for pid, k, m in notes if pid == 2 and "in your library" in m]


# ── sept 25 follow-up: progress, failed/partial, quality profile ─────────────

def test_requests_carry_progress_and_a_failed_state(client):
    c, db, _notes, _v = client
    rid = _file(c, 2).get_json()["id"]
    c.post(f"/api/video/requests/{rid}/approve")
    row = next(r for r in c.get("/api/video/requests").get_json()["requests"] if r["id"] == rid)
    assert row["state"] == "on_the_way" and row["progress"]["wanted"] == 1
    conn = db._get_connection()
    conn.execute("UPDATE video_wishlist SET status='failed' WHERE kind='movie' AND tmdb_id=438631")
    conn.commit()
    conn.close()
    row = next(r for r in c.get("/api/video/requests").get_json()["requests"] if r["id"] == rid)
    assert row["state"] == "failed"


def test_show_request_progress_counts_episodes(client):
    c, db, _notes, _v = client
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "tmdb_id": 55, "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "a", "file": {"relative_path": "/a.mkv", "size_bytes": 5}},
            {"episode_number": 2, "title": "b"}]}]})
    rid = _file(c, 2, kind="show", tmdb_id=55, title="Show").get_json()["id"]
    c.post(f"/api/video/requests/{rid}/approve")
    db.add_episodes_to_wishlist(55, "Show", [{"season_number": 1, "episode_number": 2}])
    row = next(r for r in c.get("/api/video/requests").get_json()["requests"] if r["id"] == rid)
    assert row["progress"]["owned"] == 1 and row["progress"]["wanted"] == 1
    assert row["state"] == "partial"


def test_request_quality_profile_lands_on_the_wishlist(client):
    c, db, _notes, _v = client
    conn = db._get_connection()
    cur = conn.execute("INSERT INTO quality_profiles (name, cutoff, items) VALUES ('4K', '', '[]')")
    qp = cur.lastrowid
    conn.commit()
    conn.close()
    rid = _file(c, 2, quality_profile_id=qp).get_json()["id"]
    assert db.get_video_request(rid)["quality_profile_id"] == qp
    assert _file(c, 3, tmdb_id=9, title="x", quality_profile_id=99999).status_code == 200
    c.post(f"/api/video/requests/{rid}/approve")
    conn = db._get_connection()
    got = conn.execute("SELECT quality_profile_id FROM video_wishlist WHERE tmdb_id=438631").fetchone()[0]
    conn.close()
    assert got == qp
