"""A valid API key must stamp the admin profile context (never 401 profile_required).

Regression: the Companion extension authenticates with ?api_key= and has no
cookie session. The login/launch-PIN gates already trust the key (PR #1375),
but _set_profile_context ran next, found no session profile, and answered
401 {"error": "profile_required"} on every non-/api/v1/* path — e.g.
/api/server-activity. A valid key is admin-minted, so key-authed requests
act with admin rights, mirroring require_api_key.
"""

from __future__ import annotations

import hashlib

import pytest
from flask import Flask, g

from api.auth import apply_api_key_request_context


RAW_KEY = "test-extension-key"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


class _Cfg:
    def __init__(self, keys):
        self._keys = keys

    def get(self, name, default=None):
        if name == "api_keys":
            return self._keys
        return default


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.soulsync = {"config_manager": _Cfg([{"key_hash": KEY_HASH, "label": "ext"}])}
    return app


def test_query_param_key_stamps_admin_context(app):
    with app.test_request_context(f"/api/server-activity?api_key={RAW_KEY}"):
        assert apply_api_key_request_context() is True
        assert g.is_admin is True
        assert g.can_download is True
        assert g.allowed_sides == "both"
        assert g.profile_id == 1
        assert g.profile_name == "API"


def test_bearer_header_key_stamps_admin_context(app):
    with app.test_request_context(
        "/api/server-activity", headers={"Authorization": f"Bearer {RAW_KEY}"}
    ):
        assert apply_api_key_request_context() is True
        assert g.is_admin is True
        assert g.profile_id == 1


def test_no_key_leaves_context_untouched(app):
    with app.test_request_context("/api/server-activity"):
        assert apply_api_key_request_context() is False
        assert getattr(g, "is_admin", None) is None
        assert getattr(g, "profile_id", None) is None


def test_wrong_key_leaves_context_untouched(app):
    with app.test_request_context("/api/server-activity?api_key=nope"):
        assert apply_api_key_request_context() is False
        assert getattr(g, "is_admin", None) is None


def test_existing_profile_id_is_preserved(app):
    with app.test_request_context(f"/api/server-activity?api_key={RAW_KEY}"):
        g.profile_id = 3
        g.profile_name = "Member"
        assert apply_api_key_request_context() is True
        assert g.is_admin is True
        assert g.profile_id == 3
        assert g.profile_name == "Member"
