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
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-cred-')
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


# ── Phase 2: per-profile selection (any profile selects among existing sets) ──

def test_profile_selects_among_existing_sets(client, nonadmin_profile):
    # Admin creates two Spotify sets.
    a = client.post('/api/credentials', json={'service': 'spotify', 'label': 'Acct A',
                    'payload': {'client_id': 'a', 'client_secret': 's'}}).get_json()['id']
    b = client.post('/api/credentials', json={'service': 'spotify', 'label': 'Acct B',
                    'payload': {'client_id': 'b', 'client_secret': 's'}}).get_json()['id']

    # Switch to a non-admin session — it can still READ options + SELECT.
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile

    svc = client.get('/api/profiles/me/services').get_json()['services']['spotify']
    assert {o['id'] for o in svc['options']} == {a, b}
    assert svc['selected_id'] is None
    assert 'secret' not in str(svc) and 's' not in [o.get('client_secret') for o in svc['options'] if 'client_secret' in o]

    assert client.post('/api/profiles/me/services/select',
                       json={'service': 'spotify', 'credential_id': b}).get_json()['success']
    svc = client.get('/api/profiles/me/services').get_json()['services']['spotify']
    assert svc['selected_id'] == b

    # Clear → back to None
    assert client.post('/api/profiles/me/services/select',
                       json={'service': 'spotify', 'credential_id': None}).get_json()['success']
    assert client.get('/api/profiles/me/services').get_json()['services']['spotify']['selected_id'] is None


def test_select_rejects_wrong_service_or_missing_set(client):
    sp = client.post('/api/credentials', json={'service': 'spotify', 'label': 'X',
                     'payload': {'client_id': 'a', 'client_secret': 's'}}).get_json()['id']
    # Selecting a spotify set under 'tidal' must be rejected.
    assert client.post('/api/profiles/me/services/select',
                       json={'service': 'tidal', 'credential_id': sp}).status_code == 400
    # Nonexistent id rejected.
    assert client.post('/api/profiles/me/services/select',
                       json={'service': 'spotify', 'credential_id': 999999}).status_code == 400
    # Unsupported service rejected.
    assert client.post('/api/profiles/me/services/select',
                       json={'service': 'itunes', 'credential_id': None}).status_code == 400


# ── Quick-switch: active source/server/download (admin=global, non-admin read-only) ──

def test_active_sources_read_shape(client):
    a = client.get('/api/profiles/me/active-sources').get_json()
    assert a['success'] and a['editable'] is True   # default session = admin
    assert a['metadata']['active'] and len(a['metadata']['options']) == 6
    assert len(a['server']['options']) == 4
    assert 'mode' in a['download'] and isinstance(a['download']['hybrid_order'], list)


def test_admin_sets_global_active_sources(client):
    assert client.post('/api/profiles/active-sources', json={'metadata_source': 'itunes'}).get_json()['success']
    assert client.get('/api/profiles/me/active-sources').get_json()['metadata']['active'] == 'itunes'
    # hybrid + order round-trips
    client.post('/api/profiles/active-sources', json={'download_mode': 'hybrid', 'hybrid_order': ['hifi', 'soulseek']})
    dl = client.get('/api/profiles/me/active-sources').get_json()['download']
    assert dl['mode'] == 'hybrid' and dl['hybrid_order'] == ['hifi', 'soulseek']


def test_active_sources_rejects_bad_values(client):
    assert client.post('/api/profiles/active-sources', json={'metadata_source': 'nope'}).status_code == 400
    assert client.post('/api/profiles/active-sources', json={'media_server': 'nope'}).status_code == 400
    assert client.post('/api/profiles/active-sources', json={'download_mode': 'nope'}).status_code == 400


def test_active_sources_nonadmin_readonly_and_blocked(client, nonadmin_profile):
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    assert client.get('/api/profiles/me/active-sources').get_json()['editable'] is False
    assert client.post('/api/profiles/active-sources', json={'metadata_source': 'deezer'}).status_code == 403


def test_spotify_free_composite_roundtrips_like_settings(client):
    # "Spotify (no auth)" is stored as fallback_source=spotify + spotify_free=true
    # (the same composite the Settings page uses) — the modal must report it as
    # active='spotify_free', not raw 'spotify'.
    from config.settings import config_manager
    assert client.post('/api/profiles/active-sources', json={'metadata_source': 'spotify_free'}).get_json()['success']
    assert config_manager.get('metadata.fallback_source') == 'spotify'
    assert config_manager.get('metadata.spotify_free') is True
    assert client.get('/api/profiles/me/active-sources').get_json()['metadata']['active'] == 'spotify_free'
    # Switching to plain spotify clears the flag.
    client.post('/api/profiles/active-sources', json={'metadata_source': 'spotify'})
    assert config_manager.get('metadata.spotify_free') is False
    assert client.get('/api/profiles/me/active-sources').get_json()['metadata']['active'] == 'spotify'


# ── My Accounts: per-profile connection status (Spotify) ──────────────────────

def test_connections_status_unconnected(client, nonadmin_profile):
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    body = client.get('/api/profiles/me/connections').get_json()
    assert body['success'] and body['is_admin'] is False
    assert body['connections']['spotify']['connected'] is False


def test_admin_connections_marks_admin(client):
    body = client.get('/api/profiles/me/connections').get_json()
    assert body['is_admin'] is True


def test_disconnect_admin_spotify_rejected(client):
    # Admin's Spotify is the app account (Settings) — not disconnectable here.
    assert client.post('/api/profiles/me/connections/spotify/disconnect').status_code == 400


# ── Tidal: per-profile connect status + the token-save-redirect safety ────────

def test_tidal_connection_status_and_disconnect(client, nonadmin_profile):
    db = web_server.get_database()
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    # unconnected
    assert client.get('/api/profiles/me/connections').get_json()['connections']['tidal']['connected'] is False
    # seed tokens → connected
    db.set_profile_tidal_tokens(nonadmin_profile, 'acc-tok', 'ref-tok')
    assert client.get('/api/profiles/me/connections').get_json()['connections']['tidal']['connected'] is True
    # disconnect → cleared
    assert client.post('/api/profiles/me/connections/tidal/disconnect').get_json()['success']
    assert db.get_profile_tidal(nonadmin_profile) == {}
    assert client.get('/api/profiles/me/connections').get_json()['connections']['tidal']['connected'] is False


def test_disconnect_unsupported_service_400(client, nonadmin_profile):
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    assert client.post('/api/profiles/me/connections/deezer/disconnect').status_code == 400


def test_tidal_token_refresh_redirects_to_profile_not_global(client, nonadmin_profile):
    # THE safety guarantee: a per-profile Tidal client's token save must write to
    # the PROFILE, never the global tidal_tokens slot the app runs on.
    from config.settings import config_manager
    db = web_server.get_database()
    config_manager.set('tidal_tokens', {'access_token': 'ADMIN-ACC', 'refresh_token': 'ADMIN-REF'})
    db.set_profile_tidal_tokens(nonadmin_profile, 'p-acc', 'p-ref')
    web_server.clear_profile_tidal_client(nonadmin_profile)

    c = web_server.get_tidal_client_for_profile(nonadmin_profile)
    assert c is not web_server.tidal_client            # a dedicated per-profile client
    # simulate a refresh writing new tokens
    c.access_token = 'p-acc-NEW'
    c.refresh_token = 'p-ref-NEW'
    c._save_tokens()

    assert db.get_profile_tidal(nonadmin_profile) == {'access_token': 'p-acc-NEW', 'refresh_token': 'p-ref-NEW'}
    # global slot untouched
    assert config_manager.get('tidal_tokens') == {'access_token': 'ADMIN-ACC', 'refresh_token': 'ADMIN-REF'}


def test_tidal_admin_and_unconnected_use_global_client(client):
    assert web_server.get_tidal_client_for_profile(1) is web_server.tidal_client
    assert web_server.get_tidal_client_for_profile(None) is web_server.tidal_client
    assert web_server.get_tidal_client_for_profile(987654) is web_server.tidal_client


# ── ListenBrainz: per-profile connect status + disconnect (token-paste) ───────

def test_listenbrainz_connection_status_and_disconnect(client, nonadmin_profile):
    db = web_server.get_database()
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin_profile
    # unconnected
    conns = client.get('/api/profiles/me/connections').get_json()['connections']
    assert 'listenbrainz' in conns and conns['listenbrainz']['connected'] is False
    # seed a token directly (POST validates against the live API; this tests the
    # status + disconnect wiring without a network call)
    db.set_profile_listenbrainz(nonadmin_profile, 'lb-token', '', 'lbuser')
    conns = client.get('/api/profiles/me/connections').get_json()['connections']
    assert conns['listenbrainz']['connected'] is True
    assert conns['listenbrainz']['account'] == 'lbuser'
    # disconnect via the generic endpoint
    assert client.post('/api/profiles/me/connections/listenbrainz/disconnect').get_json()['success']
    assert client.get('/api/profiles/me/connections').get_json()['connections']['listenbrainz']['connected'] is False
