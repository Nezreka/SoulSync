"""#2155: server activity (and the other admin diagnostics) is the admin's.

a regular profile could open the activity drawer and read every plex stream
on the server: who, what, which device, transcode, bandwidth, remote. every
route answered anyone. now each one 403s a non-admin and still works for an
admin or an admin-minted api key (the companion extension reads it). the
image proxy stays open (library thumbs on jellyfin load through it) but only
fetches artwork, so it can't be turned into a read of /status/sessions with
the server's own token.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parent.parent

ADMIN_KEY = "sk_admin_2155"
READ_KEY = "sk_read_2155"


class _Cfg:
    def get(self, name, default=None):
        if name == "api_keys":
            return [
                {"key_hash": hashlib.sha256(ADMIN_KEY.encode()).hexdigest(), "label": "ext"},
                {"key_hash": hashlib.sha256(READ_KEY.encode()).hexdigest(), "label": "ro", "scope": "read"},
            ]
        return default


@pytest.fixture()
def client(monkeypatch):
    import core.server_activity as sa
    from api import server_activity as routes

    monkeypatch.setattr(sa, "get_activity", lambda: {"ok": True, "sessions": [{"user": "someone"}],
                                                     "summary": {"streams": 1}})
    monkeypatch.setattr(sa, "get_history", lambda **kw: {"ok": True, "history": [{"user": "someone"}]})
    monkeypatch.setattr(sa, "get_history_group", lambda key: {"ok": True, "user": "someone"})
    monkeypatch.setattr(sa, "get_stats", lambda **kw: {"ok": True, "people": [{"user": "someone"}]})
    monkeypatch.setattr(sa, "stop_session", lambda key, msg: {"ok": True})
    monkeypatch.setattr(sa, "message_session", lambda key, msg, header="": {"ok": True})
    monkeypatch.setattr(sa, "get_settings", lambda: {"stop_presets": ["someone"]})
    monkeypatch.setattr(sa, "save_settings", lambda patch: {"stop_presets": ["someone"]})
    monkeypatch.setattr(sa, "clear_play_log", lambda: {"ok": True, "removed": 1, "who": "someone"})
    monkeypatch.setattr(sa, "fetch_avatar", lambda key: (b"someone", "image/png"))
    monkeypatch.setattr(sa, "fetch_image", lambda path: None)
    # the session side of is_admin_request: no login gate, no launch pin
    monkeypatch.setattr("core.settings.config_manager", _Cfg(), raising=False)

    app = Flask(__name__)
    app.soulsync = {"config_manager": _Cfg()}

    @app.before_request
    def _profile():
        # what web_server's before_request stamps for a cookie session
        from flask import request
        who = request.headers.get("X-Test-Profile")
        if who == "admin":
            g.profile_id, g.is_admin = 1, True
        elif who == "member":
            g.profile_id, g.is_admin = 3, False

    app.register_blueprint(routes.create_blueprint())
    return app.test_client()


READS = ["/api/server-activity", "/api/server-activity/history", "/api/server-activity/stats",
         "/api/server-activity/history/abc123", "/api/server-activity/settings",
         "/api/server-activity/avatar?user=plex:1"]
WRITES = ["/api/server-activity/stop", "/api/server-activity/message", "/api/server-activity/settings",
          "/api/server-activity/plays/clear"]


@pytest.mark.parametrize("path", READS)
def test_a_non_admin_gets_a_403_from_every_activity_read(client, path):
    r = client.get(path, headers={"X-Test-Profile": "member"})
    assert r.status_code == 403
    assert b"someone" not in r.data


@pytest.mark.parametrize("path", READS)
def test_an_admin_still_reads_them(client, path):
    r = client.get(path, headers={"X-Test-Profile": "admin"})
    assert r.status_code == 200
    assert b"someone" in r.data


@pytest.mark.parametrize("path", WRITES)
def test_every_control_is_admin_only(client, path):
    body = {"session_key": "jellyfin:abc", "message": "bye"}
    r = client.post(path, json=body, headers={"X-Test-Profile": "member"})
    assert r.status_code == 403 and b"someone" not in r.data
    r = client.post(path, json=body, headers={"X-Test-Profile": "admin"})
    assert r.status_code == 200


@pytest.mark.parametrize("path", WRITES)
def test_a_read_only_api_key_cannot_control(client, path):
    assert client.post(path + "?api_key=" + READ_KEY, json={}).status_code == 403


def test_stopping_a_stream_is_still_admin_only(client):
    body = {"session_key": "1", "message": "bye"}
    assert client.post("/api/server-activity/stop", json=body,
                       headers={"X-Test-Profile": "member"}).status_code == 403
    assert client.post("/api/server-activity/stop", json=body,
                       headers={"X-Test-Profile": "admin"}).status_code == 200


@pytest.mark.parametrize("path", READS)
def test_an_admin_api_key_keeps_working(client, path):
    r = client.get(path + ("&" if "?" in path else "?") + "api_key=" + ADMIN_KEY)
    assert r.status_code == 200


@pytest.mark.parametrize("path", READS)
def test_a_read_only_api_key_is_refused(client, path):
    assert client.get(path + ("&" if "?" in path else "?") + "api_key=" + READ_KEY).status_code == 403


def test_the_image_proxy_stays_open_for_library_thumbs(client):
    # 404 from the stubbed fetch, not the admin 403
    r = client.get("/api/server-activity/image?path=jf:abc123", headers={"X-Test-Profile": "member"})
    assert r.status_code == 404


# ── the image proxy only fetches artwork ────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/library/metadata/123/thumb/1699999999",
    "/library/metadata/123/art/1699999999",
    "/library/metadata/77/thumb",
    "/library/collections/9/composite/1700000000",
    "jf:abc123",
    "jf:5d1c2e3f-aaaa-bbbb-cccc-0123456789ab",
])
def test_artwork_paths_pass(path):
    from core.server_activity import is_image_path
    assert is_image_path(path)


@pytest.mark.parametrize("path", [
    "/status/sessions",
    "/status/sessions/history/all",
    "/accounts",
    "/library/sections",
    "/library/metadata/123",
    "/library/metadata/123/thumb/1/../../../../status/sessions",
    "/library/metadata/123/thumb?X-Plex-Token=x",
    "jf:../Sessions?",
    "jf:abc/../../Sessions",
    "jf:",
    "",
    "https://example.com/x.jpg",
])
def test_anything_else_is_refused(path):
    from core.server_activity import is_image_path
    assert not is_image_path(path)


def _fake_requests(monkeypatch, ctype, content=b"data"):
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        return SimpleNamespace(status_code=200, content=content, headers={"Content-Type": ctype})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    return seen


def test_a_plex_api_path_never_reaches_plex(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_config", lambda db=None: {"base_url": "http://plex", "token": "t"})
    seen = _fake_requests(monkeypatch, "application/xml", b"<MediaContainer/>")
    assert sa.fetch_image("/status/sessions") is None
    assert seen == []


def test_a_jellyfin_id_that_walks_out_never_reaches_jellyfin(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_jellyfin_config", lambda db=None: {"base_url": "http://jf", "api_key": "k"})
    seen = _fake_requests(monkeypatch, "application/json", b"[]")
    assert sa.fetch_image("jf:../../Sessions?x=") is None
    assert seen == []


def test_a_reply_that_is_not_an_image_is_not_forwarded(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_config", lambda db=None: {"base_url": "http://plex", "token": "t"})
    _fake_requests(monkeypatch, "application/json", b"{}")
    assert sa.fetch_image("/library/metadata/1/thumb/2") is None
    _fake_requests(monkeypatch, "image/jpeg", b"\xff\xd8")
    assert sa.fetch_image("/library/metadata/1/thumb/2") == (b"\xff\xd8", "image/jpeg")


# ── the live push and the other admin diagnostics ───────────────────────────

def _function_source(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def test_the_live_activity_room_is_admin_only():
    fn = _function_source(ROOT / "web_server.py", "handle_activity_subscribe")
    src = ast.unparse(fn)
    assert "_ws_session_profile()" in src
    # the admin check comes before the room join
    assert src.index("if not _is_admin") < src.index("join_room('activity:live')")


def _decorators(fn: ast.FunctionDef):
    return [ast.unparse(d) for d in fn.decorator_list]


@pytest.mark.parametrize("file, func", [
    ("web_server.py", "get_jellyfin_users"),          # the media server's user list
    ("web_server.py", "hydrabase_worker_pause"),      # worker control
    ("web_server.py", "hydrabase_worker_resume"),
    ("api/database_admin.py", "database_maintenance_info"),
    ("api/artist_detail.py", "debug_library_photos"),
    ("api/discover_routes.py", "diagnose_discover_data"),
    ("api/source_playlists.py", "test_database_access"),
])
def test_the_audited_diagnostics_are_admin_only(file, func):
    fn = _function_source(ROOT / file, func)
    assert "admin_only" in _decorators(fn), f"{file}:{func} answers any profile"
