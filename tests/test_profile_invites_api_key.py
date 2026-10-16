"""An API key can manage profile invites while login mode is on.

A valid key already passes the login and launch-PIN gates and gets the admin
context from before_request, but the invite routes sit behind @admin_only,
which answers from the cookie session first. With security.require_login on, a
key with no session was refused 403 on list, create and revoke, so a service
holding a key could not hand out invite links. These three routes now take
either an admin session or a valid key. Every other admin_only route is
unchanged.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-invite-key-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'invite-key.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

RAW_KEY = 'sk_test-invite-key'
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
KEY_HEADERS = {'X-API-Key': RAW_KEY}


@pytest.fixture
def login_mode(monkeypatch):
    real_get = web_server.config_manager.get

    def fake_get(name, default=None):
        if name == 'security.require_login':
            return True
        if name == 'api_keys':
            return [{'id': 'k1', 'key_hash': KEY_HASH, 'label': 'invite-bot'}]
        return real_get(name, default)

    monkeypatch.setattr(web_server.config_manager, 'get', fake_get)


@pytest.fixture
def client():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


def test_key_creates_lists_and_revokes_invites_in_login_mode(client, login_mode):
    r = client.post('/api/profiles/invites', headers=KEY_HEADERS, json={
        'preset': {'allowed_sides': 'music', 'can_download': False}, 'note': 'for a friend'})
    assert r.status_code == 201
    body = r.get_json()
    assert body['token'] and body['path'] == f"/invite/{body['token']}"

    listed = client.get('/api/profiles/invites', headers=KEY_HEADERS)
    assert listed.status_code == 200
    assert any(i['id'] == body['id'] for i in listed.get_json()['invites'])

    gone = client.delete(f"/api/profiles/invites/{body['id']}", headers=KEY_HEADERS)
    assert gone.status_code == 200


def test_no_key_and_no_session_is_still_refused_in_login_mode(client, login_mode):
    assert client.post('/api/profiles/invites', json={}).status_code in (401, 403)
    assert client.get('/api/profiles/invites').status_code in (401, 403)


def test_wrong_key_is_still_refused_in_login_mode(client, login_mode):
    bad = {'X-API-Key': 'sk_not-the-key'}
    assert client.post('/api/profiles/invites', headers=bad, json={}).status_code in (401, 403)
    assert client.get('/api/profiles/invites', headers=bad).status_code in (401, 403)


def test_a_key_does_not_open_other_admin_only_routes_in_login_mode(client, login_mode):
    """the key is let through on the invite routes only, not on admin_only generally."""
    r = client.post('/api/database/maintenance/vacuum', headers=KEY_HEADERS)
    assert r.status_code in (401, 403)
