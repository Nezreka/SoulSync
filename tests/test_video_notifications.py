"""Event notifications (arr-parity P11) — Discord / webhook / Telegram.

A second bus forwarder fans video events out to configured connections. Under
test: config normalization (a connection without a valid target never saves),
event filtering, message shaping, the dispatch fan-out (transport stubbed —
nothing here touches the network), and the admin gate on the API (connections
carry webhook URLs and bot tokens).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

import core.video.notifications as nt
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


def _discord(db, events=None, **kw):
    base = {"name": "disc", "type": "discord", "url": "https://discord.com/api/webhooks/1/x"}
    if events:
        base["events"] = events
    base.update(kw)
    return nt.save_connection(db, base)


# ---------------------------------------------------------------------------

def test_normalize_rejects_invalid_targets(db):
    assert nt.save_connection(db, {"type": "discord", "url": "not-a-url"}) is None
    assert nt.save_connection(db, {"type": "telegram", "token": "", "chat_id": "1"}) is None
    assert nt.save_connection(db, {"type": "nope", "url": "https://x"}) is None
    tg = nt.save_connection(db, {"type": "telegram", "token": "t", "chat_id": "42"})
    assert tg and tg["events"]           # defaults to the download outcomes


def test_message_shaping():
    msg = nt.format_message("video_download_completed",
                            {"title": "Severance", "season": 2, "episode": 7,
                             "quality": "1080p", "source": "torrent"})
    assert msg == "✅ Imported: Severance (S02E07 · 1080p · torrent)"
    assert nt.format_message("video_download_failed",
                             {"title": "Heat", "error": "no candidates"}).startswith("❌")


def test_handle_event_fans_out_only_to_subscribers(db, monkeypatch):
    sent = []
    monkeypatch.setattr(nt, "_send", lambda c, e, d: sent.append((c["name"], e)) or True)
    # dispatch synchronously for the test
    class _T:
        def __init__(self, target=None, **kw):
            self._t = target

        def start(self):
            self._t()
    monkeypatch.setattr(nt.threading, "Thread", _T)
    _discord(db, events=["video_download_completed"])
    _discord(db, name="fails-only", events=["video_download_failed"])
    nt.save_connection(db, {"type": "webhook", "url": "https://x/y",
                            "name": "off", "enabled": False,
                            "events": ["video_download_completed"]})
    nt.handle_event("video_download_completed", {"title": "Heat"})
    assert sent == [("disc", "video_download_completed")]
    nt.handle_event("not_a_real_event", {})
    assert len(sent) == 1


def test_send_transport_shapes(db, monkeypatch):
    calls = []

    class _Resp:
        status_code = 204

    class _Req:
        @staticmethod
        def post(url, json=None, timeout=None):
            calls.append((url, json))
            return _Resp()
    import sys
    monkeypatch.setitem(sys.modules, "requests", _Req)
    assert nt._send({"type": "discord", "url": "https://d/w", "name": "d"},
                    "video_download_completed", {"title": "Heat"}) is True
    assert calls[0][0] == "https://d/w" and "content" in calls[0][1]
    assert nt._send({"type": "telegram", "token": "tok", "chat_id": "42", "name": "t"},
                    "video_download_completed", {"title": "Heat"}) is True
    assert "api.telegram.org/bottok" in calls[1][0] and calls[1][1]["chat_id"] == "42"
    assert nt._send({"type": "webhook", "url": "https://w/h", "name": "w"},
                    "video_download_failed", {"title": "X"}) is True
    assert calls[2][1]["event"] == "video_download_failed" and "data" in calls[2][1]


def test_api_is_admin_only_and_crud(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {"profile_id": 1, "is_admin": True, "can_download": True}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)
    c = app.test_client()
    created = c.post("/api/video/notifications",
                     json={"name": "d", "type": "discord",
                           "url": "https://discord.com/api/webhooks/1/x"}).get_json()
    assert created["success"] and created["id"] == 1
    assert c.get("/api/video/notifications").get_json()["connections"][0]["name"] == "d"
    # members can't even read (tokens/URLs)
    persona.update({"profile_id": 5, "is_admin": False})
    assert c.get("/api/video/notifications").status_code == 403
    persona.update({"profile_id": 1, "is_admin": True})
    assert c.delete("/api/video/notifications/1").get_json()["success"]


def test_wiring_and_ui():
    src = open("web_server.py", encoding="utf-8").read()
    assert "from core.video.notifications import handle_event" in src
    assert 'id="vq-notify-rows"' in _INDEX and "data-vq-notify-add" in _INDEX
    assert "NOTIFY_URL" in _SETTINGS_JS and "loadNotify()" in _SETTINGS_JS
    assert "/test'" in _SETTINGS_JS or "NOTIFY_URL + '/test'" in _SETTINGS_JS
