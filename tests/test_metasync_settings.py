"""MetaSync connection settings backend (api/metasync_settings.py).

Pins: the settings view never carries a secret, secret fields only save
when non-empty, the pairing flow (request -> claim mints a SoulSync key,
hands it to the node, stores the node's local key), what happens to the
minted key on each claim outcome, the pool-key handoff body, status
pass-through and honest failure reporting, and disconnect clearing local
state whether or not the node answers.

The node's HTTP is faked throughout — these tests never touch a real
MetaSync, and the fake records every call so the exact contract SoulSync
speaks (paths, Bearer header, body fields) is pinned, not assumed.
"""

from __future__ import annotations

import json

import pytest
import requests
from flask import Flask

import api.metasync_settings as ms


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _FakeConfig:
    """Dot-notation get/set, like ConfigManager."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        node = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key, value):
        node = self._data
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeNode:
    """Stands in for the MetaSync node's /local/* surface.

    ``routes`` maps (method, path) -> _FakeResponse, or an Exception to
    raise (connection failures). Every call is recorded.
    """

    def __init__(self):
        self.routes = {}
        self.calls = []

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else url
        self.calls.append({
            "method": method, "url": url, "path": path,
            "json": json, "headers": headers or {},
        })
        outcome = self.routes.get((method, path))
        if outcome is None:
            raise AssertionError(f"unexpected node call: {method} {path}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def last_call(self, method, path):
        for call in reversed(self.calls):
            if call["method"] == method and call["path"] == path:
                return call
        raise AssertionError(f"node never got {method} {path}")


@pytest.fixture
def cfg():
    fake = _FakeConfig()
    ms.configure(config_manager=fake)
    ms._pending_pairing = None
    return fake


@pytest.fixture
def client(cfg):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ms.create_blueprint())
    return app.test_client()


@pytest.fixture
def node(monkeypatch):
    fake = _FakeNode()
    monkeypatch.setattr(ms.requests, "request", fake)
    return fake


_NODE = "http://localhost:8790"
_PAIR = {"session_id": "sess-1", "pairing_code": "123456",
         "node_fingerprint": "abc123def456"}


def _pair_ok(node):
    node.routes[("POST", "/local/pair/request")] = _FakeResponse(200, _PAIR)


def _start_pairing(client, node):
    _pair_ok(node)
    resp = client.post("/api/metasync/pair/request", json={"node_url": _NODE})
    assert resp.get_json()["success"] is True
    return resp


def _connect(client, cfg, node, local_key="mn_local_secret"):
    """Drive a full pairing + claim; returns the claim response."""
    _start_pairing(client, node)
    node.routes[("POST", "/local/pair/claim")] = _FakeResponse(200, {
        "local_api_key": local_key, "node_id": "node-42",
        "node_fingerprint": "abc123def456"})
    return client.post("/api/metasync/pair/claim")


# ---------------------------------------------------------------------------
# settings view / save
# ---------------------------------------------------------------------------

def test_view_starts_empty(client):
    data = client.get("/api/metasync").get_json()
    assert data["success"] is True
    assert data["node_url"] == "" and data["pool_url"] == ""
    assert data["pool_key_set"] is False and data["local_key_set"] is False
    assert data["pairing_pending"] is False


def test_save_settings_and_secrets_never_come_back(client, cfg):
    resp = client.put("/api/metasync/settings", json={
        "node_url": _NODE + "/", "pool_url": "https://pool.example/",
        "pool_key": "sin_live_poolsecret", "local_api_key": "mn_local_localsecret"})
    assert resp.get_json()["success"] is True
    assert cfg.get("metasync.node_url") == _NODE
    assert cfg.get("metasync.pool_url") == "https://pool.example"
    assert cfg.get("metasync.pool_key") == "sin_live_poolsecret"
    assert cfg.get("metasync.local_api_key") == "mn_local_localsecret"

    view = client.get("/api/metasync").get_json()
    assert view["pool_key_set"] is True and view["local_key_set"] is True
    blob = json.dumps(view)
    assert "sin_live_poolsecret" not in blob
    assert "mn_local_localsecret" not in blob


def test_empty_secret_fields_keep_the_stored_secret(client, cfg):
    client.put("/api/metasync/settings", json={"pool_key": "sin_live_keepme"})
    client.put("/api/metasync/settings", json={"pool_key": "", "pool_url": "https://p"})
    assert cfg.get("metasync.pool_key") == "sin_live_keepme"


def test_manual_local_key_path_lands_in_the_same_state(client, cfg):
    client.put("/api/metasync/settings", json={"local_api_key": "mn_local_pasted"})
    assert cfg.get("metasync.local_api_key") == "mn_local_pasted"
    assert client.get("/api/metasync").get_json()["local_key_set"] is True


# ---------------------------------------------------------------------------
# pairing request
# ---------------------------------------------------------------------------

def test_pair_request_needs_a_node_url(client):
    resp = client.post("/api/metasync/pair/request", json={})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_pair_request_returns_code_and_fingerprint(client, cfg, node):
    resp = _start_pairing(client, node)
    data = resp.get_json()
    assert data["pairing_code"] == "123456"
    assert data["node_fingerprint"] == "abc123def456"
    assert cfg.get("metasync.node_url") == _NODE
    view = client.get("/api/metasync").get_json()
    assert view["pairing_pending"] is True
    # the code itself is not part of the settings view — it was shown once,
    # in the pair-request response
    assert "123456" not in json.dumps(view)


def test_pair_request_conflict_is_explained(client, node):
    node.routes[("POST", "/local/pair/request")] = _FakeResponse(
        409, {"ok": False, "error": "pairing_unavailable"})
    resp = client.post("/api/metasync/pair/request", json={"node_url": _NODE})
    data = resp.get_json()
    assert data["success"] is False
    assert "already has a pairing in progress" in data["error"]


def test_pair_request_unreachable_is_honest(client, node):
    node.routes[("POST", "/local/pair/request")] = requests.ConnectionError("down")
    resp = client.post("/api/metasync/pair/request", json={"node_url": _NODE})
    data = resp.get_json()
    assert data["success"] is False
    assert "unreachable" in data["error"]


# ---------------------------------------------------------------------------
# pairing claim
# ---------------------------------------------------------------------------

def test_claim_without_pairing_is_a_400(client):
    resp = client.post("/api/metasync/pair/claim")
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_claim_happy_path_mints_key_and_stores_local_key(client, cfg, node):
    resp = _connect(client, cfg, node)
    data = resp.get_json()
    assert data["success"] is True
    assert data["node_fingerprint"] == "abc123def456"

    # the node got the contract body, carrying a real stored sk_ key
    call = node.last_call("POST", "/local/pair/claim")
    body = call["json"]
    assert body["session_id"] == "sess-1"
    assert body["pairing_code"] == "123456"
    assert body["soulsync_name"] == "SoulSync"
    assert body["soulsync_url"] == "http://localhost"
    raw = body["soulsync_api_key"]
    assert raw.startswith("sk_")
    keys = cfg.get("api_keys")
    assert len(keys) == 1 and keys[0]["label"] == "MetaSync"

    # and SoulSync stored what the node issued
    assert cfg.get("metasync.local_api_key") == "mn_local_secret"
    assert cfg.get("metasync.node_id") == "node-42"
    assert cfg.get("metasync.soulsync_key_id") == keys[0]["id"]
    view = client.get("/api/metasync").get_json()
    assert view["local_key_set"] is True and view["pairing_pending"] is False
    assert "mn_local_secret" not in json.dumps(view)


def test_claim_rejected_revokes_the_minted_key(client, cfg, node):
    _start_pairing(client, node)
    node.routes[("POST", "/local/pair/claim")] = _FakeResponse(
        401, {"ok": False, "error": "unauthorized"})
    resp = client.post("/api/metasync/pair/claim")
    data = resp.get_json()
    assert data["success"] is False
    assert "did not accept the claim" in data["error"]
    assert cfg.get("api_keys") == []
    assert not cfg.get("metasync.local_api_key")


def test_claim_unreachable_keeps_the_minted_key(client, cfg, node):
    _start_pairing(client, node)
    node.routes[("POST", "/local/pair/claim")] = requests.ConnectionError("down")
    resp = client.post("/api/metasync/pair/claim")
    data = resp.get_json()
    assert data["success"] is False
    assert "may or may not have completed" in data["error"]
    # the claim might have landed node-side: the key stays, accounted for
    keys = cfg.get("api_keys")
    assert len(keys) == 1
    assert cfg.get("metasync.soulsync_key_id") == keys[0]["id"]


def test_repair_revokes_the_previous_soulsync_key(client, cfg, node):
    assert _connect(client, cfg, node).get_json()["success"] is True
    first_id = cfg.get("api_keys")[0]["id"]
    assert _connect(client, cfg, node, local_key="mn_local_second").get_json()["success"] is True
    ids = [k["id"] for k in cfg.get("api_keys")]
    assert first_id not in ids and len(ids) == 1
    assert cfg.get("metasync.local_api_key") == "mn_local_second"


# ---------------------------------------------------------------------------
# pool-key handoff
# ---------------------------------------------------------------------------

def _pool_ready(client, cfg, node):
    assert _connect(client, cfg, node).get_json()["success"] is True
    client.put("/api/metasync/settings", json={
        "pool_url": "https://pool.example", "pool_key": "sin_live_poolkey"})


def test_handoff_needs_a_connection_first(client, cfg):
    cfg.set("metasync.pool_url", "https://pool.example")
    cfg.set("metasync.pool_key", "sin_live_poolkey")
    resp = client.post("/api/metasync/handoff", json={})
    assert resp.status_code == 400
    assert "Connect MetaSync first" in resp.get_json()["error"]


def test_handoff_sends_the_contract_body_with_bearer(client, cfg, node):
    _pool_ready(client, cfg, node)
    node.routes[("POST", "/local/handoff/pool-key")] = _FakeResponse(200, {
        "ok": True, "exchange_status": "bound", "exchange_error": None,
        "pool": {"id": "sin"}})
    resp = client.post("/api/metasync/handoff", json={})
    data = resp.get_json()
    assert data["success"] is True and data["exchange_status"] == "bound"
    call = node.last_call("POST", "/local/handoff/pool-key")
    assert call["headers"]["Authorization"] == "Bearer mn_local_secret"
    assert call["json"]["pool_url"] == "https://pool.example"
    assert call["json"]["pool_key"] == "sin_live_poolkey"
    # the spelling the shipped node reads (see metasync_settings docstring)
    assert call["json"]["pool_api_key"] == "sin_live_poolkey"


def test_handoff_binding_pending_passes_through(client, cfg, node):
    _pool_ready(client, cfg, node)
    node.routes[("POST", "/local/handoff/pool-key")] = _FakeResponse(200, {
        "ok": True, "exchange_status": "binding_pending",
        "exchange_error": "waiting", "pool": {"id": "sin"}})
    data = client.post("/api/metasync/handoff", json={}).get_json()
    assert data["success"] is True
    assert data["exchange_status"] == "binding_pending"


def test_handoff_rejected_key_and_unreachable_are_honest(client, cfg, node):
    _pool_ready(client, cfg, node)
    node.routes[("POST", "/local/handoff/pool-key")] = _FakeResponse(
        401, {"ok": False, "error": "unauthorized"})
    data = client.post("/api/metasync/handoff", json={}).get_json()
    assert data["success"] is False and "rejected the stored local key" in data["error"]

    node.routes[("POST", "/local/handoff/pool-key")] = requests.ConnectionError("down")
    data = client.post("/api/metasync/handoff", json={}).get_json()
    assert data["success"] is False and "unreachable" in data["error"]


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

_STATUS = {
    "ok": True, "service": "metasync-node", "version": "0.2.0",
    "node_id": "node-42", "node_fingerprint": "abc123def456",
    "soulsync_connected": True,
    "pools": [{"id": "sin", "name": "SIN", "url": "https://pool.example",
               "binding_status": "pending_approval", "has_pool_key": True}],
    "last_report": None,
}


def test_status_without_connection_is_honest(client, cfg):
    cfg.set("metasync.node_url", _NODE)
    data = client.get("/api/metasync/status").get_json()
    assert data["success"] is False and data["connected"] is False


def test_status_passes_the_node_payload_through(client, cfg, node):
    assert _connect(client, cfg, node).get_json()["success"] is True
    node.routes[("GET", "/local/status")] = _FakeResponse(200, _STATUS)
    data = client.get("/api/metasync/status").get_json()
    assert data["success"] is True and data["connected"] is True
    assert data["status"]["pools"][0]["binding_status"] == "pending_approval"
    call = node.last_call("GET", "/local/status")
    assert call["headers"]["Authorization"] == "Bearer mn_local_secret"


def test_status_unreachable_never_looks_connected(client, cfg, node):
    assert _connect(client, cfg, node).get_json()["success"] is True
    node.routes[("GET", "/local/status")] = requests.ConnectionError("down")
    data = client.get("/api/metasync/status").get_json()
    assert data["success"] is False and data["connected"] is False
    assert "unreachable" in data["error"]


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

def test_disconnect_notifies_node_and_clears_local_state(client, cfg, node):
    _pool_ready(client, cfg, node)
    node.routes[("POST", "/local/disconnect")] = _FakeResponse(
        200, {"ok": True, "disconnected_pools": ["sin"]})
    data = client.post("/api/metasync/disconnect").get_json()
    assert data["success"] is True and data["node_notified"] is True
    assert not cfg.get("metasync.local_api_key")
    assert cfg.get("metasync.node_id") == ""
    assert cfg.get("api_keys") == []  # the minted sk_ key was revoked
    view = client.get("/api/metasync").get_json()
    assert view["local_key_set"] is False


def test_disconnect_with_node_down_still_clears_and_says_so(client, cfg, node):
    _pool_ready(client, cfg, node)
    node.routes[("POST", "/local/disconnect")] = requests.ConnectionError("down")
    data = client.post("/api/metasync/disconnect").get_json()
    assert data["success"] is True and data["node_notified"] is False
    assert not cfg.get("metasync.local_api_key")
    assert cfg.get("api_keys") == []


# ---------------------------------------------------------------------------
# auto-detect
# ---------------------------------------------------------------------------

_HEALTH = {"ok": True, "service": "metasync-node", "version": "0.2.0"}


def test_detect_finds_the_node_on_localhost(client, cfg, node):
    node.routes[("GET", "/health")] = _FakeResponse(200, _HEALTH)
    data = client.post("/api/metasync/detect").get_json()
    assert data["success"] is True and data["found"] is True
    assert data["node_url"] == "http://localhost:8790"
    assert data["version"] == "0.2.0"
    # the first answering candidate wins — probing stops there
    assert [c["url"] for c in node.calls] == ["http://localhost:8790/health"]


def test_detect_tries_the_request_host(client, cfg, node):
    # SoulSync reached over the LAN: the node usually sits on that same
    # machine, so the request's own host is probed ahead of loopback.
    node.routes[("GET", "/health")] = _FakeResponse(200, _HEALTH)
    data = client.post("/api/metasync/detect",
                       base_url="http://192.168.1.50:8000").get_json()
    assert data["found"] is True
    assert data["node_url"] == "http://192.168.1.50:8790"
    assert node.calls[0]["url"] == "http://192.168.1.50:8790/health"


def test_detect_rejects_a_responder_that_is_not_metasync(client, cfg, node):
    # Something else squatting on 8790 must never be taken for the node.
    node.routes[("GET", "/health")] = _FakeResponse(
        200, {"ok": True, "service": "some-other-service", "version": "9.9"})
    data = client.post("/api/metasync/detect").get_json()
    assert data["success"] is True and data["found"] is False
    probed = {c["url"] for c in node.calls}
    assert probed == {"http://localhost:8790/health",
                      "http://127.0.0.1:8790/health",
                      "http://host.docker.internal:8790/health"}
    assert not cfg.get("metasync.node_url")


def test_detect_nothing_answering_is_found_false_not_an_error(client, cfg, node):
    node.routes[("GET", "/health")] = requests.ConnectionError("down")
    resp = client.post("/api/metasync/detect")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True and data["found"] is False
    assert not cfg.get("metasync.node_url")


def test_detect_probes_the_stored_url_first(client, cfg, monkeypatch):
    probed = []

    def fake_request(method, url, json=None, headers=None, timeout=None):
        probed.append((url, timeout))
        if url.startswith("http://stored-node:8790"):
            raise requests.ConnectionError("down")
        return _FakeResponse(200, _HEALTH)

    monkeypatch.setattr(ms.requests, "request", fake_request)
    cfg.set("metasync.node_url", "http://stored-node:8790")
    data = client.post("/api/metasync/detect").get_json()
    assert data["found"] is True
    # the stored URL got the first probe; the winner replaced it in config
    assert probed[0][0] == "http://stored-node:8790/health"
    assert data["node_url"] == "http://localhost:8790"
    assert cfg.get("metasync.node_url") == "http://localhost:8790"
    # every probe runs on the short detect timeout, never the 10s node one
    assert probed and all(t == ms._DETECT_TIMEOUT_SECONDS for _, t in probed)


def test_detect_stores_the_found_url(client, cfg, node):
    node.routes[("GET", "/health")] = _FakeResponse(200, _HEALTH)
    client.post("/api/metasync/detect")
    assert cfg.get("metasync.node_url") == "http://localhost:8790"
    view = client.get("/api/metasync").get_json()
    assert view["node_url"] == "http://localhost:8790"
