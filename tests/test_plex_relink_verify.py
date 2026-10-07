"""Re-link with Plex: a new token is only saved once it reaches the server
SoulSync already uses. /api/plex/verify-token is that check. It connects to
a URL the caller gives, so it's admin only."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-plexrelink-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'r.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


class _Server:
    def __init__(self, url, token, timeout=None):
        if token != 'good-token':
            raise Exception('401 Unauthorized')
        self.friendlyName = 'Boulder Plex'


@pytest.fixture
def client(monkeypatch):
    import plexapi.server
    monkeypatch.setattr(plexapi.server, 'PlexServer', _Server)
    return web_server.app.test_client()


def test_a_token_that_reaches_the_server_is_good(client):
    r = client.post('/api/plex/verify-token', json={'url': 'http://plex:32400', 'token': 'good-token'})
    assert r.get_json() == {'success': True, 'server_name': 'Boulder Plex'}


def test_a_token_that_cannot_reach_it_is_refused_without_details(client):
    r = client.post('/api/plex/verify-token', json={'url': 'http://plex:32400', 'token': 'other-account'})
    body = r.get_json()
    assert body['success'] is False and "can't reach" in body['error']
    assert '401' not in body['error']


def test_url_and_token_are_required(client):
    assert client.post('/api/plex/verify-token', json={'url': 'http://plex:32400'}).status_code == 400


def test_admin_only(client):
    db = web_server.get_database()
    pid = db.create_profile(name='RelinkNonAdmin')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    r = client.post('/api/plex/verify-token', json={'url': 'http://plex:32400', 'token': 'good-token'})
    assert r.status_code == 403
