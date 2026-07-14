"""Niche Sonarr/Radarr parity batch: the min-free-disk guard and the iCal feed.

(proper/repack preference already existed end-to-end — verified, not rebuilt.)
Disk guard: with min_free_disk_gb set, every enqueue path refuses when the
target drive is under the floor; probe failures fail OPEN (a broken probe must
never wedge downloads). iCal: /api/video/calendar.ics — Sonarr-style feed any
calendar app can subscribe to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from core.video import disk_guard
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


# ── disk guard ───────────────────────────────────────────────────────────────
def test_guard_off_by_default(tmp_path):
    assert disk_guard.has_room(str(tmp_path), {}) == (True, None)
    assert disk_guard.has_room(str(tmp_path), {"min_free_disk_gb": 0}) == (True, None)


def test_guard_compares_real_free_space(tmp_path):
    ok, free = disk_guard.has_room(str(tmp_path), {"min_free_disk_gb": 0.001})
    assert ok is True and free > 0
    ok2, _ = disk_guard.has_room(str(tmp_path), {"min_free_disk_gb": 10 ** 6})
    assert ok2 is False                          # nobody has an exabyte free


def test_guard_walks_up_to_an_existing_ancestor(tmp_path):
    target = tmp_path / "not" / "yet" / "created"
    ok, free = disk_guard.has_room(str(target), {"min_free_disk_gb": 0.001})
    assert ok is True and free is not None


def test_probe_failure_fails_open():
    assert disk_guard.has_room("", {"min_free_disk_gb": 5}) == (True, None)


def test_grab_endpoint_refuses_when_low(client, monkeypatch):
    c, db = client
    db.set_setting("movies_path", "/media/Movies")
    from core.video import disk_guard as dg
    monkeypatch.setattr(dg, "free_gb", lambda p: 0.5)
    from core.video import organization
    organization.save(db, {**organization.load(db), "min_free_disk_gb": 10})
    r = c.post("/api/video/downloads/grab",
               json={"kind": "movie", "title": "Heat", "source": "soulseek",
                     "username": "p", "filename": "f.mkv"})
    assert r.status_code == 507
    assert "nearly full" in r.get_json()["error"]


def test_drain_enqueue_skips_when_low(monkeypatch, client):
    _c, db = client
    from core.automation.handlers import video_process_wishlist as vpw
    from core.video import disk_guard as dg, organization
    organization.save(db, {**organization.load(db), "min_free_disk_gb": 10})
    monkeypatch.setattr(dg, "free_gb", lambda p: 1.0)
    started = []
    monkeypatch.setattr("core.video.slskd_download.start_download",
                        lambda *a, **k: started.append(1) or {"ok": True})
    ok = vpw._default_enqueue({"title": "Heat"}, {"username": "p", "filename": "f"},
                              [], "movie", "/media/Movies")
    assert ok is False and started == []          # refused before slskd was touched


# ── iCal feed ────────────────────────────────────────────────────────────────
def test_calendar_ics_feed(client):
    from datetime import date, timedelta
    c, db = client
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (id, server_source, server_id, tmdb_id, title) "
                 "VALUES (1,'plex','s1',100,'Severance')")
    conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (1,1,2)")
    conn.execute("INSERT INTO episodes (id, show_id, season_id, server_source, season_number, "
                 "episode_number, title, air_date) VALUES (1,1,1,'plex',2,3,"
                 "'The Board; Approves', ?)", ((date.today() + timedelta(days=2)).isoformat(),))
    conn.commit(); conn.close()

    r = c.get("/api/video/calendar.ics?scope=all")
    assert r.status_code == 200
    assert r.mimetype == "text/calendar"
    body = r.get_data(as_text=True)
    assert "BEGIN:VCALENDAR" in body and body.rstrip().endswith("END:VCALENDAR")
    assert "SUMMARY:Severance S02E03 — The Board\\; Approves" in body   # escaped ;
    assert "DTSTART;VALUE=DATE:" in body
    assert "UID:ss-100-2-3@soulsync" in body
    assert "\r\n" in body                        # RFC 5545 line endings


def test_calendar_ics_handles_an_empty_window(client):
    c, _db = client
    r = c.get("/api/video/calendar.ics")
    assert r.status_code == 200
    assert "END:VCALENDAR" in r.get_data(as_text=True)
