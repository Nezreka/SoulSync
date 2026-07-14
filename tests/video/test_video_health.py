"""Video health surface — Sonarr's health-check idea, cheap + local only.

collect(db) aggregates: unreachable library roots (error), low disk vs the
min-free floor (warning), a missing recycle override folder (warning),
errored maintenance runs (warning), and in-flight downloads with no monitor
thread (warning). No network probes — server connectivity surfaces through
the flows that use it. All-healthy = empty checks, and the dashboard strip
stays hidden.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from core.video.health import collect
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_DASH_JS = (_ROOT / "webui" / "static" / "video" / "video-dashboard.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_healthy_system_reports_nothing(db, tmp_path):
    (tmp_path / "Movies").mkdir()
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    h = collect(db)
    assert h["status"] == "ok" and h["checks"] == []


def test_unreachable_root_is_an_error(db, tmp_path):
    db.set_setting("movies_path", str(tmp_path / "not-mounted"))
    h = collect(db)
    assert h["status"] == "error"
    c = h["checks"][0]
    assert c["id"] == "movies_path" and "unreachable" in c["detail"]


def test_unset_roots_are_not_noise(db):
    assert collect(db)["checks"] == []            # nothing configured = nothing wrong


def test_low_disk_under_the_floor_warns(db, tmp_path, monkeypatch):
    (tmp_path / "Movies").mkdir()
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    from core.video import organization
    organization.save(db, {**organization.load(db), "min_free_disk_gb": 10})
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda p: 3.2)
    h = collect(db)
    assert h["status"] == "warning"
    assert "3.2 GB free" in h["checks"][0]["detail"]
    assert "grabs are paused" in h["checks"][0]["detail"]


def test_missing_recycle_override_warns(db, tmp_path):
    from core.video import organization
    organization.save(db, {**organization.load(db),
                           "recycle_path": str(tmp_path / "gone-trash")})
    h = collect(db)
    assert any(c["id"] == "recycle_path" and c["status"] == "warning" for c in h["checks"])


def test_inflight_downloads_without_monitor_warn(db, monkeypatch):
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, title, status, source) "
                 "VALUES ('movie','Heat','downloading','slskd')")
    conn.commit(); conn.close()
    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "_started", False)
    h = collect(db)
    assert any(c["id"] == "monitor" for c in h["checks"])
    # youtube rows don't count — they have their own worker pool
    conn = db._get_connection()
    conn.execute("UPDATE video_downloads SET source='youtube'")
    conn.commit(); conn.close()
    assert not any(c["id"] == "monitor" for c in collect(db)["checks"])


def test_endpoint_and_dashboard_strip(db, tmp_path):
    import api.video as videoapi
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().get("/api/video/health")
        assert r.status_code == 200 and r.get_json()["status"] in ("ok", "warning", "error")
    finally:
        videoapi._video_db = None
    assert "data-vdash-health" in _INDEX
    assert "function loadHealth" in _DASH_JS and "loadHealth();" in _DASH_JS


def test_hidden_strip_takes_no_space():
    """The strip's class rule sets display:flex, which overrides the [hidden]
    attribute's UA display:none — without an explicit [hidden] rule an empty
    healthy strip leaves a visible gap + margin above the header."""
    css = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
    assert ".vdash-health[hidden] { display: none; }" in css
