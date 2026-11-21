"""this server for Settings > Devices through the real app (PAIRING_SPEC.md
6): the address list, saving the remote address, the plain-http warning,
the pairing payload, and the reachability check end to end (the server's
own fetch is routed back into the app)."""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import uuid4

import pytest

web_server = pytest.importorskip("web_server")

from core.connect import addresses as A  # noqa: E402
from core.connect import reachability as R  # noqa: E402


def _as(pid, base_url=None):
    """a client signed in as ``pid``. the session cookie is set for the host
    the test then talks to: a cookie set for localhost isn't sent to
    192.168.1.5, and with no session the request only passed while an
    earlier test hadn't turned sign-in on (it failed in the full run)."""
    c = web_server.app.test_client()
    kwargs = {"base_url": base_url} if base_url else {}
    with c.session_transaction(**kwargs) as s:
        s.clear()
        s["profile_id"] = pid
    return c


@pytest.fixture(autouse=True)
def _clean_addresses():
    cm = web_server.config_manager
    before = (cm.get(A.REMOTE_KEY, ""), cm.get(A.CUSTOM_KEY, []))
    R._reset_for_tests()
    yield
    cm.set(A.REMOTE_KEY, before[0] or "")
    cm.set(A.CUSTOM_KEY, before[1] or [])
    R._reset_for_tests()


def test_saving_and_reading_the_addresses():
    r = _as(1).put("/api/devices/addresses", json={"remote_url": "https://music.example.com/",
                                                   "custom_urls": ["https://box.tail1.ts.net"]})
    assert r.status_code == 200, r.get_json()
    view = _as(1, "http://192.168.1.5:8008").get("/api/devices/server", base_url="http://192.168.1.5:8008").get_json()
    assert view["remote_url"] == "https://music.example.com"
    assert view["custom_urls"] == ["https://box.tail1.ts.net"]
    kinds = [a["kind"] for a in view["addresses"]]
    assert kinds[0] == "local" and view["addresses"][0]["url"] == "http://192.168.1.5:8008"
    assert kinds.index("remote") < kinds.index("custom")
    assert view["warnings"] == []
    assert view["fingerprint"] == view["lookup_id"][:12]
    assert {g["id"] for g in view["guidance"]} >= {"port-forward", "tailscale"}


def test_a_bad_address_is_refused_with_words():
    r = _as(1).put("/api/devices/addresses", json={"remote_url": "music.example.com", "custom_urls": []})
    assert r.status_code == 400 and "https://" in r.get_json()["error"]
    r = _as(1).put("/api/devices/addresses", json={"remote_url": "", "custom_urls": ["ftp://x"]})
    assert r.status_code == 400


def test_plain_http_remote_gets_the_warning():
    _as(1).put("/api/devices/addresses", json={"remote_url": "http://music.example.com", "custom_urls": []})
    view = _as(1).get("/api/devices/server").get_json()
    assert len(view["warnings"]) == 1 and "plain http" in view["warnings"][0]


def test_a_new_code_carries_the_addresses_and_their_warnings():
    _as(1).put("/api/devices/addresses", json={"remote_url": "http://music.example.com", "custom_urls": []})
    body = _as(1, "http://192.168.1.5:8008").post("/api/devices/codes", json={}, base_url="http://192.168.1.5:8008").get_json()
    assert body["payload"]["addresses"][0] == {"kind": "local", "url": "http://192.168.1.5:8008"}
    assert {"kind": "remote", "url": "http://music.example.com"} in body["payload"]["addresses"]
    assert body["warnings"] and body["payload"]["code"] == body["code"]
    _as(1).delete(f"/api/devices/codes/{body['id']}")


def test_members_get_nothing_here():
    db = web_server.get_database()
    pid = db.create_profile("srv_member_%s" % uuid4().hex[:6], "#123456", None, is_admin=False)
    try:
        for method, path in (("GET", "/api/devices/server"), ("PUT", "/api/devices/addresses"),
                             ("POST", "/api/devices/reachability"), ("GET", "/api/devices/reachability")):
            assert _as(pid).open(path, method=method, json={}).status_code == 403, path
    finally:
        db.delete_profile(pid)


class _Resp:
    def __init__(self, r):
        self.status_code = r.status_code
        self._r = r

    def json(self):
        return self._r.get_json()


def test_the_reachability_check_end_to_end(monkeypatch):
    """the server's fetch of its own remote address lands back in the app,
    through every gate, and only an answer with the nonce passes"""
    import requests

    def fetch(url, **kw):
        parts = urlsplit(url)
        client = web_server.app.test_client()
        return _Resp(client.get(parts.path, headers=kw.get("headers") or {},
                                environ_base={"REMOTE_ADDR": "203.0.113.4"}))

    monkeypatch.setattr(requests, "get", fetch)
    _as(1).put("/api/devices/addresses", json={"remote_url": "https://music.example.com", "custom_urls": []})
    r = _as(1).post("/api/devices/reachability")
    body = r.get_json()
    assert r.status_code == 200 and body["result"]["ok"], body
    probe = body["probe"]
    assert probe["url"] == "https://music.example.com" + probe["path"]
    assert [h["via"] for h in probe["hits"]] == ["self"]
    # a phone on mobile data opens the link
    phone = web_server.app.test_client().get(probe["path"], headers={"Accept": "text/html"},
                                             environ_base={"REMOTE_ADDR": "198.51.100.23"})
    assert phone.status_code == 200 and b"SoulSync answered" in phone.data
    hits = _as(1).get("/api/devices/reachability").get_json()["probe"]["hits"]
    assert hits[-1] == {**hits[-1], "ip": "198.51.100.23", "via": "outside", "from_home": False}


def test_no_remote_address_means_no_self_check():
    body = _as(1).post("/api/devices/reachability").get_json()
    assert body["result"] is None and body["probe"]["url"] is None


def test_the_probe_url_is_open_in_login_mode_and_tells_nothing_else(monkeypatch):
    probe = R.start()
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get",
                        lambda k, d=None: True if k == "security.require_login" else real_get(k, d))
    c = web_server.app.test_client()
    r = c.get(probe["path"])
    assert r.status_code == 200 and r.get_json() == {"success": True, "soulsync_probe": probe["nonce"]}
    assert c.get(R.PROBE_PATH + "made-up").status_code == 404
