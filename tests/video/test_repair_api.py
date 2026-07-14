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
    assert counts["pending"] == 3 and counts["by_job"] == {"missing_episodes": 3}

    ids = [f["id"] for f in got["items"]]
    # Fix fails cleanly (no tmdb match) → 400 with the error, stays pending.
    r = c.post(f"/api/video/repair/findings/{ids[0]}/fix", json={})
    assert r.status_code == 400 and "TMDB" in r.get_json()["error"]
    assert c.post(f"/api/video/repair/findings/{ids[0]}/dismiss", json={}).get_json()["success"]
    assert c.post(f"/api/video/repair/findings/{ids[1]}/resolve",
                  json={"action": "manual"}).get_json()["success"]
    r = c.post("/api/video/repair/findings/bulk", json={"ids": [ids[2]], "action": "dismiss"})
    assert r.get_json() == {"success": True, "updated": 1}
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
