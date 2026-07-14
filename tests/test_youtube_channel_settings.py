"""Per-channel YouTube overrides: a custom show-name (the $channel folder token) and a
quality override. Storage (KV), the enqueue/worker wiring that applies them, and the API.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_process_youtube_wishlist import enqueue_ctx
from core.video.youtube_download import quality_override_from_download
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── storage ───────────────────────────────────────────────────────────────────
def test_channel_settings_roundtrip(db):
    assert db.get_channel_settings("UC1") == {}                      # none yet
    db.set_channel_settings("UC1", {"custom_name": "My Show", "quality": {"max_resolution": "720p"}})
    cs = db.get_channel_settings("UC1")
    assert cs["custom_name"] == "My Show" and cs["quality"]["max_resolution"] == "720p"


def test_channel_settings_blank_clears(db):
    db.set_channel_settings("UC1", {"custom_name": "X"})
    db.set_channel_settings("UC1", {"custom_name": "", "quality": None})   # blanks dropped
    assert db.get_channel_settings("UC1") == {}


def test_channel_settings_isolated_per_channel(db):
    db.set_channel_settings("UC1", {"custom_name": "One"})
    assert db.get_channel_settings("UC2") == {}


# ── enqueue applies the overrides into search_ctx ─────────────────────────────
def _video():
    return {"video_id": "v1", "channel_id": "UC1", "channel_title": "Real Channel Name",
            "video_title": "Ep 1", "published_at": "2024-03-15"}


def test_enqueue_ctx_uses_channel_title_when_no_override():
    ctx = enqueue_ctx(_video(), {})
    assert ctx["channel"] == "Real Channel Name" and "quality" not in ctx


def test_enqueue_ctx_applies_custom_name_and_quality():
    ctx = enqueue_ctx(_video(), {"custom_name": "Custom Show", "quality": {"max_resolution": "4320p"}})
    assert ctx["channel"] == "Custom Show"                       # overrides the $channel token
    assert ctx["quality"] == {"max_resolution": "4320p"}
    assert ctx["video_title"] == "Ep 1" and ctx["published_at"] == "2024-03-15"


# ── worker reads the quality override back ────────────────────────────────────
def test_quality_override_from_download_reads_search_ctx():
    dl = {"search_ctx": json.dumps({"channel": "X", "quality": {"max_resolution": "720p"}})}
    assert quality_override_from_download(dl) == {"max_resolution": "720p"}


def test_quality_override_absent_returns_none():
    assert quality_override_from_download({"search_ctx": json.dumps({"channel": "X"})}) is None
    assert quality_override_from_download({"search_ctx": "{bad"}) is None
    assert quality_override_from_download({}) is None


# ── API ───────────────────────────────────────────────────────────────────────
def test_channel_settings_api_roundtrip(tmp_path):
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        r = client.post("/api/video/youtube/channel/UC1/settings",
                        json={"custom_name": "My Show", "quality": {"max_resolution": "720p"}}).get_json()
        assert r["success"] and r["settings"]["custom_name"] == "My Show"
        assert r["settings"]["quality"]["max_resolution"] == "720p"   # normalized profile

        g = client.get("/api/video/youtube/channel/UC1/settings").get_json()
        assert g["settings"]["custom_name"] == "My Show"
        assert "default_quality" in g                                 # for the 'using default' hint

        # blank custom_name + no quality clears the override
        client.post("/api/video/youtube/channel/UC1/settings", json={"custom_name": "", "quality": None})
        assert client.get("/api/video/youtube/channel/UC1/settings").get_json()["settings"] == {}
    finally:
        videoapi._video_db = None
