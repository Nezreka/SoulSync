"""#2328: a phone scanned the pairing QR and got "Couldn't reach that server
with this code". Settings > Devices now says whether this server is
connected for pairing and which addresses the app will try, docker's
bridge address is never handed to a phone, and the admin's browser
address (the host's, in docker) is handed over instead."""
from __future__ import annotations

import socket

import pytest

from core.connect import rendezvous as RV
from core.connect.identity import ServerIdentity
from core.connect.presence import PresenceClient


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("SOULSYNC_CONNECT_DISABLE", raising=False)
    RV._reset_for_tests()
    yield
    RV._reset_for_tests()


def _client() -> PresenceClient:
    return PresenceClient(
        "https://connect.ssync.net",
        ServerIdentity(key=bytes([5] * 32).hex()),
        registrations_provider=lambda: [],
        is_live=lambda _h: False,
        address_provider=lambda _o: [],
        cert_fingerprint_provider=lambda: "ef" * 32,
    )


class _Cfg:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


# -- the presence connection says where it stands -----------------------------


def test_presence_state_follows_the_connection():
    c = _client()
    assert c.state() == {"state": "connecting", "error": None}
    c._dispatch({"type": "welcome", "observed_ip": "203.0.113.7"})  # noqa: SLF001
    assert c.state()["state"] == "connected"
    c._on_error(None, ConnectionRefusedError("connection refused\nline two"))  # noqa: SLF001
    assert c.state() == {"state": "connecting", "error": "connection refused"}
    c._dispatch({"type": "welcome"})  # noqa: SLF001
    assert c.state() == {"state": "connected", "error": None}
    c._on_close(None, 1006, "")  # noqa: SLF001
    assert c.state()["state"] == "connecting"
    c._on_close(None, 4000, "displaced")  # noqa: SLF001
    assert c.state()["state"] == "displaced"


def test_pairing_status_words_for_each_state(monkeypatch):
    cfg = _Cfg()
    c = _client()
    RV._client = c  # noqa: SLF001
    st = RV.pairing_status(lambda: None, cfg)
    assert st["state"] == "connecting" and st["ready"] is False
    assert "can't find this server" in st["words"]
    c._dispatch({"type": "welcome"})  # noqa: SLF001
    st = RV.pairing_status(lambda: None, cfg)
    assert st["state"] == "connected" and st["ready"] is True
    assert st["tls_port"] == 8443 and st["listening"] is False
    c._on_close(None, 4000, "displaced")  # noqa: SLF001
    assert RV.pairing_status(lambda: None, cfg)["state"] == "displaced"
    monkeypatch.setenv("SOULSYNC_CONNECT_DISABLE", "1")
    st = RV.pairing_status(lambda: None, cfg)
    assert st["state"] == "off" and "SOULSYNC_CONNECT_DISABLE" in st["words"]


def test_pairing_status_before_presence_starts(monkeypatch):
    class Store:
        def __init__(self, fp):
            self.fp = fp

        def fingerprint(self):
            return self.fp

    monkeypatch.setattr(RV, "_identity_and_store", lambda *_a: (None, Store("")))
    monkeypatch.setattr(RV, "_runtime_thread", None)
    assert RV.pairing_status(lambda: None, _Cfg())["state"] == "off"
    monkeypatch.setattr(RV, "_runtime_thread", object())
    monkeypatch.setattr(RV, "_runtime_error", "dns01 broker said no")
    st = RV.pairing_status(lambda: None, _Cfg())
    assert st["state"] == "setting_up" and st["error"] == "dns01 broker said no"
    assert st["ready"] is False


# -- the addresses a phone gets -------------------------------------------------


@pytest.mark.parametrize("host,want", [
    ("192.168.1.5", "192.168.1.5"),
    ("10.0.0.9", "10.0.0.9"),
    ("100.101.102.103", "100.101.102.103"),
    ("172.17.0.2", None),  # docker's bridge: the container itself
    ("127.0.0.1", None),
    ("localhost", None),
    ("music.example.com", None),
    ("203.0.113.9", None),
    ("", None),
    (None, None),
])
def test_only_a_reachable_home_ip_is_a_hint(host, want):
    assert RV.usable_lan_hint(host) == want


def test_docker_bridge_never_reaches_the_answer(monkeypatch):
    class NoRoute:
        def __init__(self, *_a):
            pass

        def __enter__(self):
            raise OSError("no route")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(RV.socket, "socket", NoRoute)
    monkeypatch.setattr(RV.socket, "gethostname", lambda: "box")
    monkeypatch.setattr(RV.socket, "getaddrinfo", lambda *_a, **_k: [
        (socket.AF_INET, 0, 0, "", ("172.17.0.3", 0)),
        (socket.AF_INET, 0, 0, "", ("192.168.1.40", 0)),
    ])
    assert RV._local_lan_ips() == ["192.168.1.40"]  # noqa: SLF001


def test_the_browser_address_joins_the_answer_and_is_kept(monkeypatch):
    monkeypatch.setattr(RV, "_local_lan_ips", lambda: [])
    cfg = _Cfg()
    RV.remember_lan_hint("192.168.1.5", cfg)
    assert cfg.values[RV.LAN_HINT_KEY] == "192.168.1.5"
    assert RV._lan_ips_with_hint(cfg) == ["192.168.1.5"]  # noqa: SLF001
    # after a restart it comes back from the config
    RV._reset_for_tests()
    assert RV._lan_ips_with_hint(cfg) == ["192.168.1.5"]  # noqa: SLF001
    # a name or the bridge never replaces it
    RV.remember_lan_hint("soulsync.local", cfg)
    RV.remember_lan_hint("172.17.0.2", cfg)
    assert cfg.values[RV.LAN_HINT_KEY] == "192.168.1.5"
    targets = RV.app_targets(cfg)
    assert targets == [{"ip": "192.168.1.5", "port": 8443, "home": True}]


def test_a_hint_already_known_is_not_repeated(monkeypatch):
    monkeypatch.setattr(RV, "_local_lan_ips", lambda: ["192.168.1.5"])
    cfg = _Cfg({RV.LAN_HINT_KEY: "192.168.1.5"})
    assert RV._lan_ips_with_hint(cfg) == ["192.168.1.5"]  # noqa: SLF001


# -- through the real app ---------------------------------------------------------


def test_a_new_code_says_whether_pairing_can_work():
    web_server = pytest.importorskip("web_server")
    cm = web_server.config_manager
    before = cm.get(RV.LAN_HINT_KEY, "")
    c = web_server.app.test_client()
    base = "http://192.168.1.5:8008"
    with c.session_transaction(base_url=base) as s:
        s.clear()
        s["profile_id"] = 1
    try:
        r = c.post("/api/devices/codes", json={}, base_url=base)
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        conn = body["connect"]
        # no presence runs in tests: the code must not pretend it will work
        assert conn["ready"] is False and conn["state"] in {"off", "setting_up"}
        assert conn["words"]
        assert {"ip": "192.168.1.5", "port": 8443, "home": True} in conn["targets"]
        assert "enabled" in conn["upnp"]
        view = c.get("/api/devices/server", base_url=base).get_json()
        assert view["connect"]["state"] == conn["state"]
        c.delete(f"/api/devices/codes/{body['id']}", base_url=base)
    finally:
        cm.set(RV.LAN_HINT_KEY, before or "")
        RV._reset_for_tests()
