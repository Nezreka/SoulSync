"""Video health surface — Sonarr's health-check idea, cheap + local only.

collect(db) aggregates: unreachable library roots (error), low disk vs the
min-free floor (warning), a missing recycle override folder (warning),
errored maintenance runs (warning), in-flight downloads with no monitor
thread (warning), and an always-visible YouTube readiness tile. No network
probes — server connectivity surfaces through the flows that use it.
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


@pytest.fixture(autouse=True)
def youtube_health_defaults(monkeypatch):
    monkeypatch.setattr("core.ytdlp_update.installed_version", lambda importer=None: "2026.08.11")
    monkeypatch.setattr("core.settings.config_manager.get", lambda key, default="": default)
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda path: 100.0)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _yt_check(h):
    return next(c for c in h["checks"] if c["id"] == "youtube_health")


def test_healthy_system_reports_youtube_ready(db, tmp_path):
    (tmp_path / "Movies").mkdir()
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    h = collect(db)
    assert h["status"] == "ok"
    c = _yt_check(h)
    assert c["status"] == "ok"
    assert "yt-dlp 2026.08.11" in c["detail"]
    assert "cookies: none" in c["detail"]
    assert "temp:" in c["detail"]
    assert "output: not configured" in c["detail"]
    assert "recent failures: 0" in c["detail"]


def test_unreachable_root_is_an_error(db, tmp_path):
    db.set_setting("movies_path", str(tmp_path / "not-mounted"))
    h = collect(db)
    assert h["status"] == "error"
    c = h["checks"][0]
    assert c["id"] == "movies_path" and "unreachable" in c["detail"]


def test_unset_roots_are_not_noise(db):
    h = collect(db)
    assert h["status"] == "ok"
    assert [c["id"] for c in h["checks"]] == ["youtube_health"]


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


def test_youtube_missing_cookie_file_warns(db, monkeypatch, tmp_path):
    missing = tmp_path / "gone-cookies.txt"

    def _get(key, default=""):
        if key == "youtube.cookies_browser":
            return "custom"
        if key == "youtube.cookies_file":
            return str(missing)
        return default

    monkeypatch.setattr("core.settings.config_manager.get", _get)
    h = collect(db)
    assert h["status"] == "warning"
    c = _yt_check(h)
    assert c["status"] == "warning"
    assert "cookies: file missing" in c["detail"]


def test_youtube_recent_blocked_failures_warn(db):
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','abc123','One','failed','HTTP Error 403: Forbidden',datetime('now'))""")
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','def456','Two','failed','Sign in to confirm you are not a bot',datetime('now'))""")
    conn.commit(); conn.close()
    h = collect(db)
    c = _yt_check(h)
    assert h["status"] == "warning" and c["status"] == "warning"
    assert "2 recent YouTube failure(s)" in c["detail"]
    assert "yt-dlp blocks" in c["detail"]


def test_endpoint_and_dashboard_strip(db, tmp_path):
    import api.video as videoapi
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().get("/api/video/health")
        body = r.get_json()
        assert r.status_code == 200 and body["status"] in ("ok", "warning", "error")
        assert any(c["id"] == "youtube_health" for c in body["checks"])
    finally:
        videoapi._video_db = None
    assert "data-vdash-health" in _INDEX
    assert "function loadHealth" in _DASH_JS and "loadHealth();" in _DASH_JS
    assert "ok: '✓'" in _DASH_JS


def test_hidden_strip_takes_no_space():
    """The strip's class rule sets display:flex, which overrides the [hidden]
    attribute's UA display:none — without an explicit [hidden] rule an empty
    healthy strip leaves a visible gap + margin above the header."""
    css = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
    assert ".vdash-health[hidden] { display: none; }" in css
    assert ".vdash-health-chip--ok" in css
