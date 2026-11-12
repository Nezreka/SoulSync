"""API seam for Video Library Maintenance — the music /api/repair surface,
video-scoped: jobs list/config, findings lifecycle, history, progress."""

from __future__ import annotations

import pytest
from flask import Flask

import core.video.repair.worker as worker_mod
from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    worker_mod._worker = None                     # fresh singleton bound to this tmp DB
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        w = worker_mod._worker
        if w:
            w.stop()
        worker_mod._worker = None
        videoapi._video_db = None


def test_status_toggle_and_jobs(client):
    c, _ = client
    s = c.get("/api/video/repair/status").get_json()
    assert s["enabled"] is False and s["findings_pending"] == 0 and s["running"] is False
    assert c.post("/api/video/repair/toggle", json={"enabled": True}).get_json()["enabled"]
    assert c.get("/api/video/repair/status").get_json()["enabled"] is True

    jobs = c.get("/api/video/repair/jobs").get_json()["jobs"]
    me = next(j for j in jobs if j["job_id"] == "missing_episodes")
    assert me["display_name"] == "Missing Episodes" and me["enabled"] is False
    assert me["settings"] == {"include_specials": False}
    assert me["setting_options"] == {"include_specials": [False, True]}

    r = c.post("/api/video/repair/jobs/missing_episodes/toggle", json={"enabled": True})
    assert r.get_json() == {"job_id": "missing_episodes", "enabled": True}
    assert c.put("/api/video/repair/jobs/missing_episodes/settings",
                 json={"interval_hours": 6,
                       "settings": {"include_specials": True}}).get_json()["success"]
    me = next(j for j in c.get("/api/video/repair/jobs").get_json()["jobs"]
              if j["job_id"] == "missing_episodes")
    assert me["interval_hours"] == 6 and me["settings"]["include_specials"] is True
    assert c.post("/api/video/repair/jobs/nope/toggle", json={}).status_code == 404


def test_findings_lifecycle_over_http(client):
    c, db = client
    for i in range(3):
        db.repair_create_finding("missing_episodes", finding_type="missing_episodes",
                                 title=f"Show {i} — 1 missing episode", entity_type="show",
                                 entity_id=f"{i}:aaa",
                                 details={"show_id": i, "show_title": f"Show {i}",
                                          "tmdb_id": None,
                                          "episodes": [{"season_number": 1, "episode_number": 1}]})
    got = c.get("/api/video/repair/findings?status=pending").get_json()
    assert got["total"] == 3 and got["items"][0]["details"]["episodes"]
    counts = c.get("/api/video/repair/findings/counts").get_json()
    assert counts["pending"] == 3
    # the music shape, so one client reads both sides
    assert counts["by_job"] == {"missing_episodes": {
        "total": 3, "types": {"missing_episodes": 3}, "error": 0, "warning": 0, "info": 3}}

    ids = [f["id"] for f in got["items"]]
    # Fix fails cleanly (no tmdb match) → 400 with the error, stays pending.
    r = c.post(f"/api/video/repair/findings/{ids[0]}/fix", json={})
    assert r.status_code == 400 and "TMDB" in r.get_json()["error"]
    assert c.post(f"/api/video/repair/findings/{ids[0]}/dismiss", json={}).get_json()["success"]
    assert c.post(f"/api/video/repair/findings/{ids[1]}/resolve",
                  json={"action": "manual"}).get_json()["success"]
    r = c.post("/api/video/repair/findings/bulk", json={"ids": [ids[2]], "action": "dismiss"})
    # review 5 W3-TT: a bulk action now says how many ids it refused
    # (outside the view); none here
    assert r.get_json() == {"success": True, "updated": 1, "refused": 0}
    assert c.get("/api/video/repair/findings/counts").get_json()["pending"] == 0
    r = c.post("/api/video/repair/findings/clear", json={"status": "dismissed"})
    assert r.get_json()["deleted"] == 2
    assert c.post("/api/video/repair/findings/bulk", json={}).status_code == 400


def test_history_and_progress_endpoints(client):
    c, db = client
    rid = db.repair_record_job_start("missing_episodes")
    db.repair_record_job_finish(rid, items_scanned=7, findings_created=2)
    runs = c.get("/api/video/repair/history?job_id=missing_episodes").get_json()["runs"]
    assert len(runs) == 1 and runs[0]["items_scanned"] == 7
    assert c.get("/api/video/repair/progress").get_json() == {}


# ── posters beside findings ──────────────────────────────────────────────────

def test_a_finding_poster_comes_from_what_the_finding_names():
    from api.video.repair import finding_poster
    # one it already carries, as is
    assert finding_poster({"details": {"poster_url": "/api/video/poster/show/3515"}}) \
        == "/api/video/poster/show/3515"
    # a bare tmdb path gets the tmdb host
    assert finding_poster({"details": {"poster_url": "/abc.jpg"}}) \
        == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert finding_poster({"details": {"movie_id": 12}}) == "/api/video/poster/movie/12"
    assert finding_poster({"details": {"show_id": "7"}}) == "/api/video/poster/show/7"
    # an episode's library id is its show's
    assert finding_poster({"details": {"kind": "episode", "library_id": "3515"}}) \
        == "/api/video/poster/show/3515"
    assert finding_poster({"entity_type": "movie", "entity_id": "44", "details": {}}) \
        == "/api/video/poster/movie/44"


def test_a_finding_that_names_nothing_gets_no_poster():
    from api.video.repair import finding_poster
    assert finding_poster({"details": {}}) is None
    # a number with nothing saying movie or show is not guessed at
    assert finding_poster({"details": {"library_id": "9"}}) is None
    assert finding_poster({"entity_type": "wishlist", "entity_id": "e:90046:8:6", "details": {}}) is None
    assert finding_poster({"details": None}) is None


def test_findings_and_issue_types_carry_posters_over_http(client):
    c, db = client
    # a counter, not id(object()): a temporary's address is reused, so two
    # findings could get the same entity id and fold into one (red nightly,
    # Nov 12 2026: three rows came back instead of four)
    for n, show_id in enumerate((5, 5, 6)):
        db.repair_create_finding("missing_episodes", finding_type="missing_episodes",
                                 title=f"Show {show_id}", entity_type="show",
                                 entity_id=f"{show_id}:{n}",
                                 details={"show_id": show_id})
    db.repair_create_finding("broken_files", finding_type="broken_file", title="Stub",
                             entity_type="file", entity_id="f1", details={})
    items = c.get("/api/video/repair/findings?status=pending&sort=oldest").get_json()["items"]
    assert [i.get("album_thumb_url") for i in items] == [
        "/api/video/poster/show/5", "/api/video/poster/show/5",
        "/api/video/poster/show/6", None]
    groups = {g["finding_type"]: g
              for g in c.get("/api/video/repair/findings/groups").get_json()["groups"]}
    # newest first, one of each
    assert groups["missing_episodes"]["art"] == [
        "/api/video/poster/show/6", "/api/video/poster/show/5"]
    assert groups["broken_file"]["art"] == []
