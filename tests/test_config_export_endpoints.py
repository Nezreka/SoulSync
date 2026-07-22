"""Live HTTP tests for the config export/import endpoints (Kazimir migration).

Exercises the real Flask routes through the test client — auth gating, the
redacted-by-default response, the login-mode gate on credential export, and
the import validator — so the feature is proven end to end, not just at the
pure-logic layer.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-cfgx-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'cfgx.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


def test_export_is_admin_only(client, nonadmin):
    assert client.get('/api/config/export').status_code == 403
    assert client.post('/api/config/import', json={}).status_code == 403


def test_redacted_export_returns_a_valid_bundle(client):
    # default session = admin (profile 1)
    r = client.get('/api/config/export')
    assert r.status_code == 200
    b = r.get_json()
    assert b.get('soulsync_config_export') is True
    assert b.get('includes_secrets') is False
    assert isinstance(b.get('music'), dict) and isinstance(b.get('video'), dict)


def test_credentials_export_blocked_without_login_mode(client, monkeypatch):
    # No-login install (the default): the ?secrets=1 path must refuse so
    # plaintext creds never leave over an unauthenticated LAN.
    monkeypatch.setattr(web_server, '_require_login_enabled', lambda: False)
    r = client.get('/api/config/export?secrets=1')
    assert r.status_code == 403
    assert 'login' in (r.get_json() or {}).get('error', '').lower()


def test_credentials_export_allowed_with_login_mode(client, monkeypatch):
    # With login ON, the whole app requires an authenticated session first
    # (proving the gate). An authenticated admin then CAN pull credentials.
    monkeypatch.setattr(web_server, '_require_login_enabled', lambda: True)
    with client.session_transaction() as sess:
        sess['profile_id'] = 1
        sess['login_authenticated'] = True
    r = client.get('/api/config/export?secrets=1')
    assert r.status_code == 200
    assert r.get_json().get('includes_secrets') is True


def test_import_rejects_a_non_bundle(client):
    r = client.post('/api/config/import', json={'not': 'a bundle'})
    assert r.status_code == 400
    assert (r.get_json() or {}).get('success') is False


def test_import_accepts_a_valid_bundle_round_trip(client):
    exported = client.get('/api/config/export').get_json()
    r = client.post('/api/config/import', json=exported)
    assert r.status_code == 200
    j = r.get_json()
    assert j.get('success') is True and 'music_keys' in j
