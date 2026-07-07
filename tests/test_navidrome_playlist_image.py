"""NavidromeClient.set_playlist_image (#993).

Subsonic (the API this client normally uses) has no playlist-cover field, so the
mirrored source cover is pushed via Navidrome's NATIVE API: a JWT login with the
same username/password, then a multipart POST to /api/playlist/{id}/image. These
tests drive the real method with `requests` stubbed and assert the native path —
including that the record-blanking PUT /api/playlist/{id} is never used.
"""

from __future__ import annotations

from types import SimpleNamespace

from core import navidrome_client as ncmod
from core.navidrome_client import NavidromeClient


class _Resp:
    def __init__(self, ok=True, json_data=None, content=b"", headers=None, status_code=200):
        self.ok = ok
        self._json = json_data or {}
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self._json


def _client():
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = lambda: True
    c.base_url = "http://nav.local"
    c.username = "u"
    c.password = "p"
    c.get_playlist_by_name = lambda name: SimpleNamespace(id="PL1")
    return c


def test_set_playlist_image_uploads_via_native_api(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(("POST", url, kw))
        if url.endswith("/auth/login"):
            return _Resp(ok=True, json_data={"token": "TKN"})
        if url.endswith("/api/playlist/PL1/image"):
            return _Resp(ok=True)
        return _Resp(ok=False, status_code=404)

    def fake_get(url, **kw):
        calls.append(("GET", url, kw))
        return _Resp(ok=True, content=b"IMGDATA", headers={"Content-Type": "image/png"})

    monkeypatch.setattr(ncmod.requests, "post", fake_post)
    monkeypatch.setattr(ncmod.requests, "get", fake_get)

    assert _client().set_playlist_image("My PL", "http://img/cover.png") is True

    # Native login used the SAME creds already in config.
    login = [x for x in calls if x[1].endswith("/auth/login")][0]
    assert login[2]["json"] == {"username": "u", "password": "p"}

    # Image POSTed to the native endpoint with the Bearer token + multipart file.
    up = [x for x in calls if x[1].endswith("/api/playlist/PL1/image")][0]
    assert up[0] == "POST"
    assert up[2]["headers"]["x-nd-authorization"] == "Bearer TKN"
    assert "image" in up[2]["files"]

    # NEVER the record-blanking PUT /api/playlist/{id}.
    assert not any(method == "PUT" for (method, _u, _k) in calls)


def test_set_playlist_image_returns_false_when_playlist_missing(monkeypatch):
    posted = []
    monkeypatch.setattr(ncmod.requests, "post", lambda url, **kw: posted.append(url) or _Resp())
    monkeypatch.setattr(ncmod.requests, "get", lambda url, **kw: _Resp())

    c = _client()
    c.get_playlist_by_name = lambda name: None
    assert c.set_playlist_image("Nope", "http://img/x.png") is False
    assert posted == []   # no login, no upload attempted


def test_set_playlist_image_returns_false_when_login_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(ncmod.requests, "post",
                        lambda url, **kw: calls.append(url) or _Resp(ok=False, status_code=401))
    monkeypatch.setattr(ncmod.requests, "get",
                        lambda url, **kw: _Resp(ok=True, content=b"x"))

    assert _client().set_playlist_image("My PL", "http://img/x.png") is False
    assert calls == ["http://nav.local/auth/login"]   # login only, no upload


def test_set_playlist_image_returns_false_without_image_url(monkeypatch):
    # No cover stored → no-op, and crucially no network calls.
    called = []
    monkeypatch.setattr(ncmod.requests, "post", lambda *a, **k: called.append("post") or _Resp())
    monkeypatch.setattr(ncmod.requests, "get", lambda *a, **k: called.append("get") or _Resp())
    assert _client().set_playlist_image("My PL", "") is False
    assert called == []
