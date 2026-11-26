"""the address list, the pairing payload and the reachability check
(PAIRING_SPEC.md 6)."""

from __future__ import annotations

import json

import pytest

from core.connect import addresses as A
from core.connect import reachability as R


def _get(values):
    return lambda key, default=None: values.get(key, default)


# ── addresses ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("https://music.example.com", "https://music.example.com"),
    ("https://music.example.com/", "https://music.example.com"),
    ("HTTPS://Music.Example.com:8443/soulsync/", "https://music.example.com:8443/soulsync"),
    ("http://100.101.102.103:8008", "http://100.101.102.103:8008"),
    ("http://[fd00::5]:8008", "http://[fd00::5]:8008"),
])
def test_clean_url_keeps_what_a_device_needs(raw, want):
    assert A.clean_url(raw) == (want, None)


@pytest.mark.parametrize("raw", [
    "music.example.com", "ftp://music.example.com", "https://", "https://user:pw@music.example.com",
    "https://music.example.com/?x=1", "https://music.example.com/#frag", "https://music.example.com:99999",
])
def test_clean_url_refuses_with_words(raw):
    url, why = A.clean_url(raw)
    assert url is None and why


def test_blank_is_no_address():
    assert A.clean_url("  ") == (None, None)


@pytest.mark.parametrize("host,home", [
    ("192.168.1.5", True), ("10.0.0.2", True), ("172.20.1.1", True), ("100.64.0.9", True),
    ("nas.local", True), ("fd00::1", True), ("203.0.113.9", False), ("music.example.com", False),
    ("8.8.8.8", False),
])
def test_home_addresses(host, home):
    assert A.is_home_address(host) is home


def test_local_first_then_remote_then_extra_no_repeats():
    get = _get({A.REMOTE_KEY: "https://music.example.com/",
                A.CUSTOM_KEY: ["https://box.tail1234.ts.net", "https://music.example.com", "bad"]})
    out = A.address_list(get, request_host_url="http://192.168.1.5:8008/",
                         own_ips=["192.168.1.5", "172.17.0.3", "203.0.113.4", "10.0.0.8"])
    assert out == [
        {"kind": "local", "url": "http://192.168.1.5:8008"},
        {"kind": "local", "url": "http://10.0.0.8:8008"},
        {"kind": "remote", "url": "https://music.example.com"},
        {"kind": "custom", "url": "https://box.tail1234.ts.net"},
    ]


def test_a_browser_on_the_public_address_is_not_a_local_one():
    out = A.address_list(_get({}), request_host_url="https://music.example.com/", own_ips=[])
    assert out == []


def test_plain_http_outside_gets_a_loud_warning_local_does_not():
    addrs = [{"kind": "local", "url": "http://192.168.1.5:8008"},
             {"kind": "remote", "url": "http://music.example.com"},
             {"kind": "custom", "url": "https://box.ts.net"}]
    warned = A.warnings(addrs)
    assert len(warned) == 1 and "http://music.example.com" in warned[0] and "https" in warned[0]


def test_the_pairing_payload_and_its_qr_text():
    payload = A.pairing_payload(lookup_id="ab" * 32, fingerprint="ab" * 6,
                                addresses=[{"kind": "local", "url": "http://192.168.1.5:8008"}],
                                code="7KQ4-M2XD-9FHA-T3VB")
    text = A.payload_text(payload)
    assert json.loads(text) == {"v": 1, "lookup_id": "ab" * 32, "fingerprint": "ab" * 6,
                                "addresses": [{"kind": "local", "url": "http://192.168.1.5:8008"}],
                                "code": "7KQ4-M2XD-9FHA-T3VB"}
    assert " " not in text


def test_the_qr_is_a_link_a_phone_camera_opens():
    import base64

    payload = A.pairing_payload(lookup_id="ab" * 32, fingerprint="ab" * 6,
                                addresses=[{"kind": "local", "url": "http://192.168.1.5:8008"}],
                                code="7KQ4-M2XD-9FHA-T3VB")
    link = A.pairing_link(payload)
    assert link.startswith("soulsync://pair?d=")
    data = link[len("soulsync://pair?d="):]
    # url safe as is: no padding, no + or /
    assert "=" not in data and "+" not in data and "/" not in data
    decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
    assert decoded == A.payload_text(payload)


# ── reachability ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_probes():
    R._reset_for_tests()
    yield
    R._reset_for_tests()


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_a_probe_is_one_time_and_runs_out():
    p = R.start(now=1000.0)
    assert p["path"] == R.PROBE_PATH + p["nonce"] and p["seconds_left"] == 600
    assert R.record_hit(p["nonce"], "203.0.113.7", now=1001.0)
    assert not R.record_hit("not-a-probe", "203.0.113.7", now=1001.0)
    assert not R.record_hit(p["nonce"], "203.0.113.7", now=1000.0 + 600)
    assert R.view(p["nonce"], now=1000.0 + 600) is None


def test_a_phone_outside_is_told_apart_from_home_and_from_the_server():
    p = R.start(now=1000.0)
    R.record_hit(p["nonce"], "192.168.1.30", now=1001.0)
    R.record_hit(p["nonce"], "203.0.113.7", now=1002.0)
    R.record_hit(p["nonce"], "203.0.113.8", self_marker="guessed", now=1003.0)
    hits = R.view(p["nonce"], now=1004.0)["hits"]
    assert [(h["ip"], h["via"], h["from_home"]) for h in hits] == [
        ("192.168.1.30", "outside", True), ("203.0.113.7", "outside", False),
        ("203.0.113.8", "outside", False)]


def test_the_self_check_passes_only_when_this_server_answers():
    p = R.start()
    seen = {}

    def fetch(url, **kw):
        seen["url"] = url
        seen["headers"] = kw["headers"]
        R.record_hit(p["nonce"], "203.0.113.4", self_marker=kw["headers"][R.SELF_HEADER])
        return _Resp(200, {"soulsync_probe": p["nonce"]})

    out = R.self_check("https://music.example.com", p["nonce"], fetch=fetch)
    assert out["ok"] and out["code"] == "ok"
    assert seen["url"] == "https://music.example.com" + R.PROBE_PATH + p["nonce"]
    assert R.view(p["nonce"])["hits"][0]["via"] == "self"


@pytest.mark.parametrize("resp,code", [
    (_Resp(200, {"soulsync_probe": "someone-else"}), "not_this_server"),
    (_Resp(200, ValueError("html")), "not_this_server"),
    (_Resp(403, {}), "blocked"),
])
def test_the_self_check_says_what_went_wrong(resp, code):
    p = R.start()
    out = R.self_check("https://music.example.com", p["nonce"], fetch=lambda url, **kw: resp)
    assert not out["ok"] and out["code"] == code and out["words"]


@pytest.mark.parametrize("exc,code", [
    (TimeoutError("timed out"), "timeout"),
    (OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "certificate"),
    (OSError("Failed to resolve 'music.example.com'"), "dns"),
    (ConnectionRefusedError("refused"), "unreachable"),
])
def test_network_failures_get_words(exc, code):
    p = R.start()

    def fetch(url, **kw):
        raise exc

    out = R.self_check("https://music.example.com", p["nonce"], fetch=fetch)
    assert not out["ok"] and out["code"] == code and "music.example.com" in out["words"]


def test_guidance_covers_the_four_ways_in():
    assert {g["id"] for g in R.GUIDANCE} == {"port-forward", "cloudflare-tunnel", "tailscale", "own-domain"}
