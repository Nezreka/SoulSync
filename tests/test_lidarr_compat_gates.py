"""lidarr compatible api through the real app and its gates (#1799).

seerr-style apps send only X-Api-Key, no cookie session. with login mode on
they still have to get through, and a read key still has to be stopped.
"""

from __future__ import annotations

import hashlib

import pytest

from api import auth

web_server = pytest.importorskip('web_server')


def _key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


ADMIN_RAW, READ_RAW = 'sk_gate-admin', 'sk_gate-read'
KEYS = [{'id': 'a', 'key_hash': _key(ADMIN_RAW), 'label': 'seerr', 'scope': 'admin'},
        {'id': 'r', 'key_hash': _key(READ_RAW), 'label': 'dash', 'scope': 'read'}]
STATUS = '/lidarr/api/v1/system/status'


@pytest.fixture
def setup(monkeypatch):
    state = {'lidarr_compat.enabled': True, 'security.require_login': True}
    real_get = web_server.config_manager.get

    def get(name, default=None):
        if name == 'api_keys':
            return KEYS
        if name in state:
            return state[name]
        return real_get(name, default)

    monkeypatch.setattr(web_server.config_manager, 'get', get)
    monkeypatch.setattr(web_server.config_manager, 'set', lambda *a, **k: None)
    monkeypatch.setattr(auth, '_should_persist_usage', lambda *a, **k: False)
    client = web_server.app.test_client()
    with client.session_transaction() as s:
        s.clear()
    return client, state


def test_admin_key_gets_through_login_mode(setup):
    client, _ = setup
    resp = client.get(STATUS, headers={'X-Api-Key': ADMIN_RAW})
    assert resp.status_code == 200, resp.data
    assert resp.get_json()['appName'] == 'Lidarr'


def test_no_key_is_stopped_by_login_mode(setup):
    client, _ = setup
    assert client.get(STATUS).status_code == 401


def test_read_key_is_stopped_before_the_route(setup):
    client, _ = setup
    assert client.get(STATUS, headers={'X-Api-Key': READ_RAW}).status_code == 403


def test_without_login_mode_the_route_still_wants_an_admin_key(setup):
    client, state = setup
    state['security.require_login'] = False
    assert client.get(STATUS).status_code == 401
    assert client.get(STATUS, headers={'X-Api-Key': ADMIN_RAW}).status_code == 200


def test_turned_off_is_a_404_even_with_a_key(setup):
    client, state = setup
    state['lidarr_compat.enabled'] = False
    assert client.get(STATUS, headers={'X-Api-Key': ADMIN_RAW}).status_code == 404


def test_soulsync_own_v1_status_is_untouched(setup):
    # /lidarr sits beside /api/v1, it must not shadow soulsync's own api
    client, _ = setup
    resp = client.get('/api/v1/system/status', headers={'X-Api-Key': ADMIN_RAW})
    assert resp.status_code == 200
    assert resp.get_json().get('appName') != 'Lidarr'
