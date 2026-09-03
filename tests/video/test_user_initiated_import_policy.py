from __future__ import annotations

import json


def test_wishlist_manual_enqueue_marks_user_replace_policy(monkeypatch):
    import core.automation.handlers.video_process_wishlist as w

    captured = {}

    class _DB:
        def add_video_download(self, row):
            captured.update(row)
            return 1
        def get_setting(self, key):
            return ""

    import api.video as videoapi
    monkeypatch.setattr(videoapi, "get_video_db", lambda: _DB())
    monkeypatch.setattr("core.video.disk_guard.has_room", lambda target, settings: (True, 100))
    monkeypatch.setattr("core.video.organization.load", lambda db: {})
    monkeypatch.setattr("core.video.slskd_download.start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("core.video.download_monitor.ensure_started", lambda *a, **k: None)

    item = {"_user_initiated": True, "title": "Heat", "tmdb_id": 603, "year": 1995}
    best = {"source": "soulseek", "username": "peer", "filename": "Heat.1995.1080p.mkv",
            "title": "Heat.1995.1080p", "quality_label": "1080p"}
    out = w._default_enqueue(item, best, [best], "movie", "/movies")

    assert out["ok"] is True
    ctx = json.loads(captured["search_ctx"])
    assert ctx["user_initiated"] is True
    assert ctx["import_policy"] == "user_replace"


def test_api_manual_grab_marks_user_replace_policy(tmp_path, monkeypatch):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    from flask import Flask, g

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    videoapi._video_db = db
    try:
        monkeypatch.setattr("core.video.disk_guard.has_room", lambda target, settings: (True, 100))
        monkeypatch.setattr("core.video.organization.load", lambda db: {})
        monkeypatch.setattr("core.video.client_grab.grab", lambda *a, **k: {"ok": True, "ref": "r1"})
        monkeypatch.setattr("core.video.download_monitor.ensure_started", lambda *a, **k: None)

        app = Flask(__name__)

        @app.before_request
        def _stamp():
            g.is_admin = True
            g.can_download = True

        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        res = app.test_client().post("/api/video/downloads/grab", json={
            "source": "torrent", "kind": "movie", "title": "Heat", "release_title": "Heat.1995.1080p",
            "download_url": "magnet:?xt=urn:btih:abc", "search_ctx": {"scope": "movie", "title": "Heat"},
        })

        assert res.status_code == 200
        row = db.list_video_downloads()[0]
        ctx = json.loads(row["search_ctx"])
        assert ctx["user_initiated"] is True
        assert ctx["import_policy"] == "user_replace"
    finally:
        videoapi._video_db = None
