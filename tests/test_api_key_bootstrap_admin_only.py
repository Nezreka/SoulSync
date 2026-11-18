"""the first api key is the admin's to mint.

/api/v1 skips the login gate (its routes check their own key), and the
bootstrap route needs no key because none exists yet. together that let
anyone who could reach the server mint an admin key on an install with no
keys, signed in or not. the route now asks who's calling: a signed-in admin,
or, with no login and no launch pin, the only profile there is.
"""

from __future__ import annotations

import pytest
from flask import Blueprint, Flask


class _Cfg:
    def __init__(self):
        self.data = {}

    def get(self, name, default=None):
        return self.data.get(name, default)

    def set(self, name, value):
        self.data[name] = value


class _Security:
    """stands in for core.settings.config_manager's security switches"""

    def __init__(self, **values):
        self.values = values

    def get(self, name, default=None):
        return self.values.get(name, default)


@pytest.fixture
def boot(monkeypatch):
    import api.settings as settings_api
    import core.profile_context as profile_context
    import core.settings as core_settings
    from api.settings import register_routes

    state = {"profiles": 1, "admins": {1}}
    monkeypatch.setattr(settings_api, "_profile_count", lambda: state["profiles"])
    monkeypatch.setattr(profile_context, "is_admin_profile",
                        lambda pid, **kw: pid in state["admins"])

    def make(security=None):
        monkeypatch.setattr(core_settings, "config_manager", _Security(**(security or {})))
        cfg = _Cfg()
        app = Flask(__name__)
        app.secret_key = "test"
        app.soulsync = {"config_manager": cfg}
        bp = Blueprint("api_v1_boot_admin", __name__)
        register_routes(bp)
        app.register_blueprint(bp, url_prefix="/api/v1")
        return app, cfg

    return make, state


def _post(client):
    return client.post("/api/v1/api-keys/bootstrap", json={"label": "x"})


def test_signed_out_with_login_on_gets_no_key(boot):
    make, _ = boot
    app, cfg = make({"security.require_login": True})
    resp = _post(app.test_client())
    assert resp.status_code == 403
    assert not cfg.data.get("api_keys"), "a stranger must not mint the admin's key"


def test_launch_pin_not_entered_gets_no_key(boot):
    make, _ = boot
    app, cfg = make({"security.require_pin_on_launch": True})
    assert _post(app.test_client()).status_code == 403
    assert not cfg.data.get("api_keys")


def test_signed_in_admin_with_login_on_mints_it(boot):
    make, _ = boot
    app, cfg = make({"security.require_login": True})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["login_authenticated"] = True
        sess["profile_id"] = 1
    assert _post(client).status_code == 201
    assert len(cfg.data["api_keys"]) == 1


def test_signed_in_member_gets_no_key(boot):
    make, state = boot
    state["profiles"] = 2
    app, cfg = make({"security.require_login": True})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["login_authenticated"] = True
        sess["profile_id"] = 2
    assert _post(client).status_code == 403
    assert not cfg.data.get("api_keys")


def test_no_session_on_a_shared_install_is_not_taken_for_the_admin(boot):
    # no login, no pin, several profiles: a bare request reads as profile 1,
    # but nothing says it's the admin asking
    make, state = boot
    state["profiles"] = 3
    app, cfg = make()
    assert _post(app.test_client()).status_code == 403
    assert not cfg.data.get("api_keys")


def test_the_admins_own_session_on_a_shared_install_mints_it(boot):
    make, state = boot
    state["profiles"] = 3
    app, cfg = make()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["profile_id"] = 1
    assert _post(client).status_code == 201


def test_a_single_user_install_keeps_the_curl_first_run(boot):
    # the documented first run: no login, no pin, one profile
    make, _ = boot
    app, cfg = make()
    assert _post(app.test_client()).status_code == 201


def test_cant_tell_who_is_asking_refuses(boot, monkeypatch):
    import api.settings as settings_api
    make, _ = boot

    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(settings_api, "_profile_count", boom)
    app, cfg = make()
    assert _post(app.test_client()).status_code == 403
