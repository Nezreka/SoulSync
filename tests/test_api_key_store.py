"""API key list / create / revoke live in one place (api/key_store.py).

The Settings page routes (/api/v1/api-keys-internal/*, session auth) and the
API routes (/api/v1/api-keys, key auth) used to carry separate copies of this
logic and their responses had drifted apart. Both now call key_store, and the
read-check-write steps run under one lock.
"""

from __future__ import annotations

import threading
import time

import pytest
from flask import Blueprint, Flask

from api import key_store
from api.auth import _hash_key


class _Cfg:
    """Minimal config manager; get() returns the live value like ConfigManager."""

    def __init__(self, keys=None, slow=0.0):
        self.data = {"api_keys": list(keys or [])}
        self.slow = slow

    def get(self, name, default=None):
        value = self.data.get(name, default)
        if self.slow:
            time.sleep(self.slow)  # widen the read-to-write window
        return value

    def set(self, name, value):
        self.data[name] = value


# ---- key_store -------------------------------------------------------------

def test_create_then_list_never_exposes_hash_or_raw_key():
    cfg = _Cfg()
    raw, record = key_store.create_key(cfg, "bot")
    listed = key_store.list_keys(cfg)
    assert listed == [key_store.public_view(record)]
    assert "key_hash" not in listed[0]
    assert raw not in str(listed)
    assert cfg.data["api_keys"][0]["key_hash"] == _hash_key(raw)


def test_create_does_not_mutate_the_list_it_read():
    cfg = _Cfg()
    before = cfg.data["api_keys"]
    key_store.create_key(cfg, "a")
    assert before == []  # copy-on-write: readers holding the old list see it unchanged


def test_revoke_removes_only_that_key():
    cfg = _Cfg()
    _, a = key_store.create_key(cfg, "a")
    _, b = key_store.create_key(cfg, "b")
    assert key_store.revoke_key(cfg, a["id"]) is True
    assert [k["id"] for k in cfg.data["api_keys"]] == [b["id"]]


def test_revoke_unknown_id_is_false_and_writes_nothing():
    cfg = _Cfg()
    key_store.create_key(cfg, "a")
    before = cfg.data["api_keys"]
    assert key_store.revoke_key(cfg, "nope") is False
    assert cfg.data["api_keys"] is before


@pytest.mark.parametrize("bad", ["not-a-dict", None, 42, ["a", "list"]])
def test_malformed_entry_does_not_break_list_or_revoke(bad):
    # An imported config can hold anything in api_keys; one odd entry used to
    # fail the list (and revoke) for every key.
    cfg = _Cfg()
    _, a = key_store.create_key(cfg, "a")
    cfg.data["api_keys"] = [bad] + cfg.data["api_keys"]
    assert [k["id"] for k in key_store.list_keys(cfg)] == [a["id"]]
    assert key_store.revoke_key(cfg, a["id"]) is True
    assert cfg.data["api_keys"] == [bad]


def test_bootstrap_only_while_empty():
    cfg = _Cfg()
    assert key_store.bootstrap_key(cfg, "first") is not None
    assert key_store.bootstrap_key(cfg, "second") is None
    assert len(cfg.data["api_keys"]) == 1


def test_concurrent_bootstraps_mint_exactly_one_key():
    cfg = _Cfg(slow=0.02)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(key_store.bootstrap_key(cfg)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    minted = [r for r in results if r is not None]
    assert len(minted) == 1
    assert cfg.data["api_keys"] == [minted[0][1]]


def test_concurrent_create_and_revoke_lose_nothing():
    cfg = _Cfg(slow=0.01)
    _, victim = key_store.create_key(cfg, "victim")
    created = []
    threads = [threading.Thread(target=lambda: key_store.revoke_key(cfg, victim["id"]))]
    threads += [
        threading.Thread(target=lambda: created.append(key_store.create_key(cfg, "n")[1]))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = {k["id"] for k in cfg.data["api_keys"]}
    assert victim["id"] not in ids
    assert ids == {r["id"] for r in created}


# ---- /api/v1/api-keys routes ------------------------------------------------

@pytest.fixture()
def v1_app():
    from api.settings import register_routes

    cfg = _Cfg()
    raw, _ = key_store.create_key(cfg, "admin-key")
    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}
    bp = Blueprint("api_v1_test", __name__)
    register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")
    return app, cfg, {"Authorization": f"Bearer {raw}"}


def test_v1_routes_create_list_revoke(v1_app):
    app, cfg, auth = v1_app
    client = app.test_client()

    resp = client.post("/api/v1/api-keys", json={"label": "bot"}, headers=auth)
    assert resp.status_code == 201
    made = resp.get_json()["data"]
    assert set(made) == {"key", "id", "label", "key_prefix", "created_at"}

    listed = client.get("/api/v1/api-keys", headers=auth).get_json()["data"]["keys"]
    assert made["id"] in {k["id"] for k in listed}

    assert client.delete(f"/api/v1/api-keys/{made['id']}", headers=auth).status_code == 200
    resp = client.delete(f"/api/v1/api-keys/{made['id']}", headers=auth)
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "NOT_FOUND"


def test_v1_bootstrap_refuses_once_a_key_exists(v1_app):
    app, _, _ = v1_app
    resp = app.test_client().post("/api/v1/api-keys/bootstrap", json={})
    assert resp.status_code == 403


def test_v1_bootstrap_on_empty_install():
    from api.settings import register_routes

    app = Flask(__name__)
    app.soulsync = {"config_manager": _Cfg()}
    bp = Blueprint("api_v1_boot", __name__)
    register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")
    resp = app.test_client().post("/api/v1/api-keys/bootstrap", json={"label": "first"})
    assert resp.status_code == 201
    assert resp.get_json()["data"]["label"] == "first"


def _v1_app_with(cfg):
    from api.settings import register_routes

    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}
    bp = Blueprint(f"api_v1_{id(cfg)}", __name__)
    register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")
    return app


def test_v1_concurrent_bootstraps_through_the_route_mint_one_key():
    cfg = _Cfg(slow=0.02)
    app = _v1_app_with(cfg)
    codes = []

    def hit():
        codes.append(app.test_client().post("/api/v1/api-keys/bootstrap", json={}).status_code)

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 403, 403, 403]
    assert len(cfg.data["api_keys"]) == 1


def test_v1_revoke_racing_a_create_through_the_routes_stays_revoked():
    cfg = _Cfg()
    raw, admin = key_store.create_key(cfg, "admin")
    _, victim = key_store.create_key(cfg, "victim")
    cfg.slow = 0.02
    app = _v1_app_with(cfg)
    auth = {"Authorization": f"Bearer {raw}"}

    threads = [
        threading.Thread(target=lambda: app.test_client().delete(
            f"/api/v1/api-keys/{victim['id']}", headers=auth)),
        threading.Thread(target=lambda: app.test_client().post(
            "/api/v1/api-keys", json={"label": "new"}, headers=auth)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    labels = sorted(k["label"] for k in cfg.data["api_keys"])
    assert labels == ["admin", "new"]


# ---- auth's last_used_at write ----------------------------------------------

def test_a_revoke_during_a_key_request_stays_revoked(monkeypatch):
    # require_api_key reads the key list, then (throttled) writes last_used_at
    # back. It used to write the list it read, so a revoke landing between the
    # read and the write was undone and the revoked key worked again.
    import api.auth as auth

    monkeypatch.setattr(auth, "_last_persisted_usage", {})

    class RevokeMidRequest(_Cfg):
        armed = False

        def get(self, name, default=None):
            value = super().get(name, default)
            if self.armed:
                self.armed = False
                key_store.revoke_key(self, victim["id"])
            return value

    cfg = RevokeMidRequest()
    raw, admin = key_store.create_key(cfg, "admin")
    _, victim = key_store.create_key(cfg, "victim")
    cfg.armed = True
    app = _v1_app_with(cfg)

    resp = app.test_client().get("/api/v1/api-keys", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    assert [k["id"] for k in cfg.data["api_keys"]] == [admin["id"]]
    assert cfg.data["api_keys"][0]["last_used_at"] is not None


def test_last_used_at_is_persisted_without_touching_other_keys(monkeypatch):
    import api.auth as auth

    monkeypatch.setattr(auth, "_last_persisted_usage", {})
    cfg = _Cfg()
    raw, _ = key_store.create_key(cfg, "a")
    _, other = key_store.create_key(cfg, "b")
    before = cfg.data["api_keys"]
    app = _v1_app_with(cfg)

    app.test_client().get("/api/v1/api-keys", headers={"Authorization": f"Bearer {raw}"})
    assert cfg.data["api_keys"] is not before  # a fresh list was written
    stamped = {k["label"]: k["last_used_at"] for k in cfg.data["api_keys"]}
    assert stamped["a"] is not None and stamped["b"] is None


@pytest.mark.parametrize("bad", ["not-a-dict", None, 42])
def test_malformed_entry_does_not_break_key_auth(bad, monkeypatch):
    import api.auth as auth

    monkeypatch.setattr(auth, "_last_persisted_usage", {})
    cfg = _Cfg()
    raw, _ = key_store.create_key(cfg, "a")
    cfg.data["api_keys"] = [bad] + cfg.data["api_keys"]
    app = _v1_app_with(cfg)

    resp = app.test_client().get("/api/v1/api-keys", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    assert cfg.data["api_keys"][0] == bad
