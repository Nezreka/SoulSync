"""Phase 1: service-credential-set admin endpoints (real app, real HTTP).

These import the actual web_server app and drive the endpoints through a Flask
test client — the only way to verify the @admin_only gating and the request
validation wrappers for real. Secrets must never come back in any response.

Heavy (imports web_server once), so isolated in its own module. The default
session is profile 1 (admin); a non-admin session is simulated to prove the
gate blocks writes.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='ss-cred-ep-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'creds_ep.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin_profile():
    """Create a real non-admin profile and yield its id."""
    db = web_server.get_database()
    pid = db.create_profile(name=f'tester_{os.urandom(3).hex()}', avatar_color='#fff')
    yield pid


# ── admin happy paths ────────────────────────────────────────────────────────

def test_admin_create_list_update_delete_roundtrip(client):
    r = client.post('/api/credentials', json={
        'service': 'plex', 'label': 'Living Room',
        'payload': {'base_url': 'http://plex:32400', 'token': 'sekret'}})
    assert r.status_code == 200 and r.get_json()['success']
    cid = r.get_json()['id']

    # list shows it, and NEVER leaks the payload/secret
    body = client.get('/api/credentials').get_json()
    assert any(c['label'] == 'Living Room' for c in body['services']['plex'])
    assert 'sekret' not in str(body) and 'payload' not in str(body)

    # update label
    assert client.put(f'/api/credentials/{cid}', json={'label': 'Den'}).get_json()['success']
    body = client.get('/api/credentials').get_json()
    assert any(c['label'] == 'Den' for c in body['services']['plex'])

    # delete
    assert client.delete(f'/api/credentials/{cid}').get_json()['success']
    body = client.get('/api/credentials').get_json()
    assert not any(c['id'] == cid for c in body['services']['plex'])


# ── validation ───────────────────────────────────────────────────────────────

def test_create_rejects_missing_fields(client):
    r = client.post('/api/credentials', json={
        'service': 'plex', 'label': 'X', 'payload': {'base_url': 'http://p'}})
    assert r.status_code == 400 and 'token' in r.get_json()['error']


def test_create_rejects_unsupported_service(client):
    r = client.post('/api/credentials', json={'service': 'itunes', 'label': 'X', 'payload': {}})
    assert r.status_code == 400


def test_create_rejects_blank_label(client):
    r = client.post('/api/credentials', json={
        'service': 'deezer', 'label': '  ', 'payload': {'arl': 'x'}})
    assert r.status_code == 400


def test_duplicate_label_conflict(client):
    p = {'service': 'qobuz', 'label': 'Dup', 'payload': {'user_auth_token': 't'}}
    assert client.post('/api/credentials', json=p).status_code == 200
    assert client.post('/api/credentials', json=p).status_code == 409


def test_update_missing_set_404(client):
    assert client.put('/api/credentials/999999', json={'label': 'x'}).status_code == 404


def test_delete_missing_set_404(client):
    assert client.delete('/api/credentials/999999').status_code == 404


# ── the security gate: non-admin cannot manage credential sets ───────────────

def test_nonadmin_blocked_from_all_credential_writes(client, nonadmin_profile):
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    assert client.get('/api/credentials').status_code == 403
    assert client.post('/api/credentials', json={
        'service': 'plex', 'label': 'Sneaky',
        'payload': {'base_url': 'http://p', 'token': 't'}}).status_code == 403
    assert client.put('/api/credentials/1', json={'label': 'x'}).status_code == 403
    assert client.delete('/api/credentials/1').status_code == 403
