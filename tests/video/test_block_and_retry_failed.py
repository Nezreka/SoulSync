"""Block and retry every failed download at once (c5pie, Discord Nov 4 2026).

A retry per row fired together would start an indexer search per row at the
same moment; this blocks every failed release first, then searches the
titles one at a time on a single background thread.
"""

from __future__ import annotations

import json

import pytest

from core.video import download_monitor as monitor
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    import core.video.download_events as events
    monkeypatch.setattr(events, "publish", lambda *a, **k: None)
    searched = []
    monkeypatch.setattr(monitor, "_search_again_now",
                        lambda db_, dl, wait=False: searched.append((dl["id"], wait)) or True)

    class _Inline:
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(monitor.threading, "Thread", _Inline)
    vdb.searched = searched
    return vdb


def _row(db, status, *, release, episode=1, kind="episode", source="soulseek"):
    ctx = {"scope": "episode", "title": "Show", "season": 1, "episode": episode}
    dl_id = db.add_video_download({
        "kind": kind, "title": "Show", "status": status, "source": source,
        "username": "peer", "filename": release, "release_title": release,
        "media_id": "1399", "media_source": "tmdb", "search_ctx": json.dumps(ctx),
        "candidates": "[]", "tried_files": "[]", "tried_queries": "[]"})
    return dl_id


def test_every_failed_release_is_blocked_then_searched_in_turn(db):
    a = _row(db, "failed", release="Show.S01E01-BAD")
    b = _row(db, "failed", release="Show.S01E02-BAD", episode=2)
    _row(db, "completed", release="Show.S01E03-OK", episode=3)
    _row(db, "import_failed", release="Show.S01E04-SAMPLE", episode=4)

    assert monitor.block_and_retry_failed(db) == {"blocked": 2, "searching": 2}
    assert db.video_blocklist_pairs() == {("peer", "Show.S01E01-BAD"), ("peer", "Show.S01E02-BAD")}
    assert db.searched == [(a, True), (b, True)], "one at a time, waiting on each"


def test_youtube_rows_are_left_alone(db):
    _row(db, "failed", release="yt-video", kind="youtube")
    assert monitor.block_and_retry_failed(db) == {"blocked": 0, "searching": 0}
    assert db.searched == []


def test_the_route_is_admin_only(tmp_path, monkeypatch, db):
    import api.video as videoapi
    from flask import Flask
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        role = {"admin": False}

        @app.before_request
        def _who():
            from flask import g
            g.profile_id = 1 if role["admin"] else 2
            g.is_admin = role["admin"]
            g.can_download = True

        monkeypatch.setattr(monitor, "ensure_started", lambda *a, **k: None)
        http = app.test_client()
        assert http.post("/api/video/downloads/blocklist/retry-failed").status_code == 403
        role["admin"] = True
        _row(db, "failed", release="Show.S01E01-BAD")
        res = http.post("/api/video/downloads/blocklist/retry-failed").get_json()
        assert res == {"ok": True, "blocked": 1, "searching": 1}
    finally:
        videoapi._video_db = None
