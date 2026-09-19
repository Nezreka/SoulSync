"""the admin switches a profile to its own library through PUT /api/profiles/<id> (#1199)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ownlib-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'ownlib.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def sam():
    return web_server.get_database().create_profile(name=f'sam_{os.urandom(3).hex()}')


def test_admin_switches_a_profile_to_its_own_library(client, sam):
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': '/music/sam'})
    assert r.status_code == 200 and r.get_json()['success'], r.data
    assert web_server.get_database().get_profile_library(sam) == {'mode': 'own', 'root': '/music/sam'}
    listed = next(p for p in client.get('/api/profiles').get_json()['profiles'] if p['id'] == sam)
    assert (listed['library_mode'], listed['library_root']) == ('own', '/music/sam')
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'shared'})
    assert r.get_json()['success'] and web_server.get_database().get_profile_library(sam)['mode'] == 'shared'


def test_an_own_library_without_a_folder_or_on_the_shared_folder_is_refused(client, sam, monkeypatch):
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': ''}).status_code == 400
    monkeypatch.setattr(web_server.config_manager, 'get', lambda k, d=None: '/music/shared' if k == 'soulseek.transfer_path' else d)
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': '/music/shared/'})
    assert r.status_code == 400 and 'shared' in r.get_json()['error']
    assert web_server.get_database().get_profile_library(sam)['mode'] == 'shared'


def test_the_admin_profile_cannot_be_given_an_own_library(client):
    r = client.put('/api/profiles/1', json={'library_mode': 'own', 'library_root': '/music/x'})
    assert r.status_code == 400


def test_a_non_admin_cannot_switch_their_own_library_mode(client, sam):
    with client.session_transaction() as sess:
        sess['profile_id'] = sam
    r = client.put(f'/api/profiles/{sam}', json={'name': 'Sam', 'library_mode': 'own', 'library_root': '/music/sam'})
    assert r.status_code == 200          # the name save is theirs to make
    assert web_server.get_database().get_profile_library(sam)['mode'] == 'shared', "a non-admin set their own library mode"
