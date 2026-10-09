"""Every key-auth path reads the key the same way and matches it the same way.

require_api_key (the v1 decorator) and request_has_valid_api_key (the session
gates) used to parse the request separately and had drifted: one stripped
whitespace from the Bearer value and one didn't. Both now go through
_request_api_key and _match_api_key, and X-API-Key is accepted as a third
transport.
"""

from __future__ import annotations

import hashlib

import pytest
from flask import Flask, g

from api import auth
from api.auth import (
    apply_api_key_request_context,
    request_has_valid_api_key,
    require_api_key,
)


RAW_KEY = "sk_test-transport-key"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


class _Cfg:
    """Minimal config manager. get() returns the live list, like ConfigManager."""

    def __init__(self, keys):
        self.data = {"api_keys": keys}
        self.saved = []

    def get(self, name, default=None):
        return self.data.get(name, default)

    def set(self, name, value):
        self.data[name] = value
        self.saved.append(list(value))


@pytest.fixture(autouse=True)
def _reset_usage_cache():
    with auth._usage_lock:
        auth._last_persisted_usage.clear()
    yield
    with auth._usage_lock:
        auth._last_persisted_usage.clear()


@pytest.fixture()
def cfg():
    return _Cfg([{"id": "k1", "key_hash": KEY_HASH, "label": "bot"}])


@pytest.fixture()
def app(cfg):
    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}

    @app.route("/api/v1/thing")
    @require_api_key
    def thing():
        return {"ok": True, "profile_name": g.profile_name}

    return app


TRANSPORTS = {
    "bearer": ({"Authorization": f"Bearer {RAW_KEY}"}, ""),
    "bearer_padded": ({"Authorization": f"Bearer  {RAW_KEY} "}, ""),
    "bearer_lowercase": ({"Authorization": f"bearer {RAW_KEY}"}, ""),
    "x_api_key": ({"X-API-Key": RAW_KEY}, ""),
    "x_api_key_lowercase": ({"x-api-key": RAW_KEY}, ""),
    "query": ({}, f"?api_key={RAW_KEY}"),
}


@pytest.mark.parametrize("name", sorted(TRANSPORTS))
def test_decorator_accepts_every_transport(app, name):
    headers, qs = TRANSPORTS[name]
    resp = app.test_client().get(f"/api/v1/thing{qs}", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["profile_name"] == "bot"


@pytest.mark.parametrize("name", sorted(TRANSPORTS))
def test_session_gate_accepts_every_transport(app, name):
    headers, qs = TRANSPORTS[name]
    with app.test_request_context(f"/api/server-activity{qs}", headers=headers):
        assert request_has_valid_api_key() is True
        assert apply_api_key_request_context() is True
        assert g.is_admin is True
        assert g.profile_name == "API"


def test_missing_key_is_401_and_names_all_transports(app):
    resp = app.test_client().get("/api/v1/thing")
    assert resp.status_code == 401
    body = resp.get_data(as_text=True)
    assert "Bearer" in body and "X-API-Key" in body and "api_key" in body


@pytest.mark.parametrize("headers,qs", [
    ({"Authorization": "Bearer sk_wrong"}, ""),
    ({"X-API-Key": "sk_wrong"}, ""),
    ({}, "?api_key=sk_wrong"),
])
def test_wrong_key_rejected_on_both_paths(app, headers, qs):
    resp = app.test_client().get(f"/api/v1/thing{qs}", headers=headers)
    assert resp.status_code == 403
    with app.test_request_context(f"/api/x{qs}", headers=headers):
        assert request_has_valid_api_key() is False


def test_bearer_wins_over_other_transports(app):
    # A valid Bearer key is used even when a wrong X-API-Key / query key rides along.
    resp = app.test_client().get(
        "/api/v1/thing?api_key=sk_wrong",
        headers={"Authorization": f"Bearer {RAW_KEY}", "X-API-Key": "sk_wrong"},
    )
    assert resp.status_code == 200


def test_empty_bearer_falls_through_to_query(app):
    resp = app.test_client().get(
        f"/api/v1/thing?api_key={RAW_KEY}", headers={"Authorization": "Bearer "}
    )
    assert resp.status_code == 200


def test_record_without_hash_never_matches(cfg):
    cfg.data["api_keys"] = [{"id": "broken"}]
    assert auth._match_api_key(cfg, RAW_KEY) is None
    assert auth._match_api_key(cfg, "") is None


@pytest.mark.parametrize("bad", [
    {"id": "non_ascii", "key_hash": "\u00e9" * 64},
    {"id": "not_a_str", "key_hash": 12345},
    {"id": "bytes", "key_hash": b"abc"},
    "not-a-dict",
])
def test_bad_record_ahead_of_a_good_one_does_not_block_it(cfg, bad):
    # compare_digest raises TypeError on a non-ascii str and .encode() on a
    # non-str; either way a bad record first must not block the keys after it.
    good = {"id": "k1", "key_hash": KEY_HASH, "label": "bot"}
    cfg.data["api_keys"] = [bad, good]
    assert auth._match_api_key(cfg, RAW_KEY) is good


def test_usage_write_does_not_restore_a_key_revoked_mid_request(app, cfg, monkeypatch):
    """The last_used_at write must persist the current list, not the one read
    before the lookup. Revoking during the request used to be undone."""
    real_should_persist = auth._should_persist_usage

    def revoke_then_persist(key_hash, now):
        # Simulate a revoke landing between the lookup and the usage write.
        cfg.set("api_keys", [k for k in cfg.get("api_keys") if k["id"] != "k1"])
        return real_should_persist(key_hash, now)

    monkeypatch.setattr(auth, "_should_persist_usage", revoke_then_persist)
    resp = app.test_client().get(
        "/api/v1/thing", headers={"Authorization": f"Bearer {RAW_KEY}"}
    )

    assert resp.status_code == 200  # the request itself was already authenticated
    assert cfg.get("api_keys") == []
    assert all(k["id"] != "k1" for k in cfg.saved[-1])


def test_usage_write_persists_last_used(app, cfg):
    app.test_client().get("/api/v1/thing", headers={"X-API-Key": RAW_KEY})
    assert cfg.saved, "first use should persist"
    assert cfg.saved[-1][0]["last_used_at"]
