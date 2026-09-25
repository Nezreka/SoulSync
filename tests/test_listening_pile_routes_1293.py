"""#1293 through the real app: the web player's play lands in the listener's
pile, and connecting or dropping your own listenbrainz starts or stops your
import.

heavy (imports web_server once), so it lives in its own module like the other
route tests.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-pile-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'pile_routes.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def db():
    return web_server.get_database()


def _as(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


def _artists(db, pid):
    return [a['name'] for a in db.get_top_artists('all', 50, profile_id=pid)]


def _log(client, artist):
    r = client.post('/api/library/log-play', json={
        'track': {'title': f'{artist} song', 'artist': artist, 'album': 'A'},
        'duration_ms': 1000,
    })
    assert r.get_json()['success'] is True


def test_a_web_player_play_goes_in_the_listeners_pile(client, db):
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    bob = db.create_profile(name=f'bob_{os.urandom(3).hex()}')
    db.set_profile_listenbrainz(kim, 'token', '', 'kim_lb')
    kim_artist = f'kim-{os.urandom(3).hex()}'
    bob_artist = f'bob-{os.urandom(3).hex()}'

    _as(client, kim)
    _log(client, kim_artist)
    _as(client, bob)
    _log(client, bob_artist)

    # kim has her own listenbrainz, so her play is hers alone
    assert kim_artist in _artists(db, kim)
    assert kim_artist not in _artists(db, 1)
    # bob doesn't, so his play stays in the shared pile he reads, like before
    assert bob_artist in _artists(db, 1)
    assert bob_artist in _artists(db, bob)
    assert bob_artist not in _artists(db, kim)


def test_connecting_and_dropping_listenbrainz_drives_the_import(client, db, monkeypatch):
    import api.user_profiles as up

    calls = []

    class Workers:
        def on_connected(self, pid, previous, username):
            calls.append(('connected', pid, previous, username))

        def on_disconnected(self, pid):
            calls.append(('disconnected', pid))

    monkeypatch.setattr(up, '_listenbrainz_import_workers', lambda: Workers())
    monkeypatch.setattr(up, '_validate_lb_token', lambda token, base_url='': (True, 'new_lb'))
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    db.set_profile_listenbrainz(kim, 'old-token', '', 'old_lb')
    _as(client, kim)

    assert client.post('/api/profiles/me/listenbrainz', json={'token': 'new-token'}).get_json()['success']
    assert client.delete('/api/profiles/me/listenbrainz').get_json()['success']

    assert calls == [('connected', kim, 'old_lb', 'new_lb'), ('disconnected', kim)]


class _Recorder:
    def __init__(self):
        self.calls = []

    def on_connected(self, pid, previous, username):
        self.calls.append(('connected', pid, previous, username))

    def on_disconnected(self, pid):
        self.calls.append(('disconnected', pid))


def _fake_lastfm(monkeypatch, *, readable=True):
    """last.fm answers for anyone with a public profile, and nothing (None)
    for an unknown name or hidden listening. never the network."""
    import core.lastfm_client as lastfm_client

    class FakeLastFM:
        def __init__(self, **_kw):
            pass

        def get_user_recent_tracks(self, username, limit=200, **_kw):
            if not readable:
                return None
            return {'recenttracks': {'@attr': {'user': username.title()}, 'track': []}}

    monkeypatch.setattr(lastfm_client, 'LastFMClient', FakeLastFM)


def test_a_profile_saves_its_lastfm_username_and_its_import_starts(client, db, monkeypatch):
    import api.user_profiles as up

    fm = _Recorder()
    monkeypatch.setattr(up, '_lastfm_import_workers', lambda: fm)
    monkeypatch.setattr(up.config_manager, 'get',
                        lambda key, default=None: 'app-key' if key == 'lastfm.api_key' else default)
    _fake_lastfm(monkeypatch)
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    _as(client, kim)

    r = client.post('/api/profiles/me/lastfm', json={'username': 'kim_fm'}).get_json()

    # last.fm's own spelling of the name is what gets saved
    assert r == {'success': True, 'username': 'Kim_Fm'}
    assert db.get_profile_lastfm(kim)['username'] == 'Kim_Fm'
    assert fm.calls == [('connected', kim, '', 'Kim_Fm')]
    conns = client.get('/api/profiles/me/connections').get_json()['connections']
    assert conns['lastfm'] == {'connected': True, 'account': 'Kim_Fm'}

    assert client.post('/api/profiles/me/connections/lastfm/disconnect').get_json()['success']
    assert db.get_profile_lastfm(kim)['username'] == ''
    assert fm.calls[-1] == ('disconnected', kim)


def test_an_unreadable_lastfm_name_is_refused_and_nothing_is_saved(client, db, monkeypatch):
    import api.user_profiles as up

    fm = _Recorder()
    monkeypatch.setattr(up, '_lastfm_import_workers', lambda: fm)
    monkeypatch.setattr(up.config_manager, 'get',
                        lambda key, default=None: 'app-key' if key == 'lastfm.api_key' else default)
    _fake_lastfm(monkeypatch, readable=False)
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    _as(client, kim)

    r = client.post('/api/profiles/me/lastfm', json={'username': 'hidden_fm'})

    assert r.status_code == 400
    assert 'hidden' in r.get_json()['error']
    assert db.get_profile_lastfm(kim)['username'] == ''
    assert fm.calls == []


def test_without_an_api_key_the_profile_is_told_to_ask_the_admin(client, db, monkeypatch):
    import api.user_profiles as up

    monkeypatch.setattr(up.config_manager, 'get', lambda key, default=None: default)
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    _as(client, kim)

    r = client.post('/api/profiles/me/lastfm', json={'username': 'kim_fm'})

    assert r.status_code == 400
    assert 'API key' in r.get_json()['error']


def test_the_admin_keeps_lastfm_in_settings(client, db):
    _as(client, 1)
    r = client.post('/api/profiles/me/lastfm', json={'username': 'admin_fm'})
    assert r.status_code == 400
    assert db.get_profile_lastfm(1)['username'] == ''


def test_my_account_says_whose_listening_the_profile_reads(client, db):
    """the card at the top of My Account: shared until the profile connects its
    own listenbrainz or last.fm, then its own, naming the sources."""
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    _as(client, kim)

    def listening():
        return client.get('/api/profiles/me/connections').get_json()['listening']

    assert listening() == {'scope': 'shared', 'sources': []}
    db.set_profile_lastfm(kim, 'kim_fm')
    assert listening() == {'scope': 'profile', 'sources': ['lastfm']}
    db.set_profile_listenbrainz(kim, 'token', '', 'kim_lb')
    assert listening() == {'scope': 'profile', 'sources': ['listenbrainz', 'lastfm']}
