"""navidrome calls share one kept-alive session (TheHomeGuy's pi-hole).

a bare requests.get per call opened a new connection, and with it a dns
lookup, every time: 1,000+ lookups an hour. these pin that every subsonic
call goes through the one module session, and that the session keeps no
cookies, since profiles acting as their own navidrome user share it."""

import http.cookiejar

import requests

import core.navidrome_client as nc
from core.navidrome_client import NavidromeClient


def _client():
    c = NavidromeClient()
    c.base_url = "http://navidrome:4533"
    c.username = "u"
    c.password = "p"
    c._connection_attempted = True
    return c


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"subsonic-response": {"status": "ok"}}


def test_reads_and_writes_go_through_the_one_session(monkeypatch):
    calls = []

    class _Session:
        def get(self, url, **kw):
            calls.append(("get", url))
            return _Resp()

        def post(self, url, **kw):
            calls.append(("post", url))
            return _Resp()

    shared = _Session()
    monkeypatch.setattr(nc, "_http", lambda: shared)

    def no_bare_call(*a, **kw):
        raise AssertionError("a subsonic call bypassed the shared session")

    monkeypatch.setattr(requests, "get", no_bare_call)
    monkeypatch.setattr(requests, "post", no_bare_call)

    c = _client()
    assert c._make_request("getArtists") is not None
    assert c._make_request("createPlaylist", {"name": "x"}) is not None
    assert [k for k, _ in calls] == ["get", "post"]


def test_the_session_is_shared_and_refuses_cookies(monkeypatch):
    monkeypatch.setattr(nc, "_http_session", None)
    first = nc._http()
    assert nc._http() is first
    policy = first.cookies.get_policy()
    assert isinstance(policy, http.cookiejar.DefaultCookiePolicy)
    req = requests.Request("GET", "http://navidrome:4533/rest/ping").prepare()
    cookie = http.cookiejar.Cookie(
        0, "sid", "x", None, False, "navidrome", False, False, "/", False,
        False, None, False, None, None, {})
    assert not policy.set_ok(cookie, requests.cookies.MockRequest(req))
