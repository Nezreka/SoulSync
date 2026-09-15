"""video side can't connect to jellyfin 12 while music can, same key (#1250).

the #1232 fix taught JellyfinClient to send the modern Authorization header
alongside X-Emby-Token. it never reached the code that talks to jellyfin with
bare requests: the video connection test, the video users picker, the video
source's refresh/scan/poster/collection calls and server activity. each of
those still hand-rolled {"X-Emby-Token": key} and jellyfin 12 answered 401,
so the test button said "rejected the api key" for a key that worked fine
on the music tab.

one helper now builds the header pair and every direct call site uses it.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import requests

from core.jellyfin_client import JellyfinClient, jellyfin_auth_headers

ROOT = Path(__file__).resolve().parents[1]


def test_helper_sends_both_headers_and_matches_the_client():
    headers = jellyfin_auth_headers("abc123")
    assert headers["X-Emby-Token"] == "abc123"
    assert headers["Authorization"].startswith("MediaBrowser ")
    assert 'Token="abc123"' in headers["Authorization"]

    client = JellyfinClient.__new__(JellyfinClient)
    client.api_key = "abc123"
    assert client._auth_header() == headers["Authorization"]


def test_helper_tolerates_a_missing_key():
    headers = jellyfin_auth_headers(None)
    assert headers["X-Emby-Token"] == ""
    assert 'Token=""' in headers["Authorization"]


def test_video_connection_test_sends_the_modern_header(monkeypatch):
    from core.video.sources import video_jellyfin_test
    seen = []

    def _get(url, headers=None, timeout=None, **kw):
        seen.append(headers)
        r = MagicMock()
        r.status_code = 200
        r.json = lambda: {"ServerName": "Test"} if url.endswith("/System/Info") else [{"Id": "u1"}]
        return r

    monkeypatch.setattr(requests, "get", _get)
    ok, msg = video_jellyfin_test({"base_url": "http://jelly.invalid/", "api_key": "abc123"})
    assert ok, msg
    assert seen, "no request made"
    for h in seen:
        assert h.get("X-Emby-Token") == "abc123"
        assert h.get("Authorization", "").startswith("MediaBrowser ")


def test_video_source_direct_calls_send_the_modern_header():
    from core.video.sources import JellyfinVideoSource
    client = MagicMock()
    client.base_url = "http://jelly.invalid/"
    client.api_key = "abc123"
    src = JellyfinVideoSource.__new__(JellyfinVideoSource)
    src._c = client
    base, headers = src._jf()
    assert base == "http://jelly.invalid"
    assert headers["X-Emby-Token"] == "abc123"
    assert headers["Authorization"].startswith("MediaBrowser ")


def test_video_users_endpoint_sends_the_modern_header(monkeypatch, tmp_path):
    from flask import Flask
    import api.video as videoapi
    from database.video_database import VideoDatabase
    import core.video.sources as sources
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    seen = {}

    def _get(url, headers=None, timeout=None, **kw):
        seen.update(headers or {})
        r = MagicMock()
        r.status_code = 200
        r.json = lambda: [{"Id": "u1", "Name": "a", "Policy": {"IsAdministrator": True}}]
        return r

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(sources, "video_jellyfin_config",
                        lambda *a, **k: {"base_url": "http://jelly.invalid", "api_key": "abc123"})
    resp = app.test_client().get("/api/video/jellyfin/users")
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["success"] is True
    assert seen.get("X-Emby-Token") == "abc123"
    assert seen.get("Authorization", "").startswith("MediaBrowser ")


def test_server_activity_sends_the_modern_header(monkeypatch):
    from core import server_activity as sa
    seen = {}

    def _get(url, headers=None, timeout=None, **kw):
        seen.update(headers or {})
        r = MagicMock()
        r.status_code = 200
        r.json = lambda: []
        return r

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(sa, "_jellyfin_config",
                        lambda db: {"base_url": "http://jelly.invalid", "api_key": "abc123"})
    sa._jellyfin_activity(None)
    assert seen.get("X-Emby-Token") == "abc123"
    assert seen.get("Authorization", "").startswith("MediaBrowser ")


def test_no_hand_rolled_jellyfin_header_dict_anywhere():
    # a bare {"X-Emby-Token": ...} literal is exactly the bug: works on 10.x,
    # 401 on 12, and nothing in the logs says why. the helper is the only place
    # allowed to spell the header name in a dict.
    bare = re.compile(r"""\{\s*['"]X-Emby-Token['"]\s*:""")
    offenders = []
    for folder in ("core", "api", "web_server.py"):
        path = ROOT / folder
        files = [path] if path.is_file() else path.rglob("*.py")
        for f in files:
            if f.name == "jellyfin_client.py":
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            if bare.search(text):
                offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, offenders
