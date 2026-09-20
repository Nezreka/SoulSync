"""a plex home user per profile (#1265): the playlists a profile syncs land
on their own plex user.

plex writes playlists as whoever the connection's token is, so every
playlist soulsync wrote belonged to the app account. a profile can now link
itself to a home user: the admin token switches to that user once (their
plex profile pin when they have one, used for that switch and not kept) and
the user's own server access token is stored. the sync then runs through
PlexUserView, a PlexClient with its own connection as that user.

hermetic: plexapi's account and server are fakes here. the same flows ran
once against boulder's real server with the "Kids" home user and behaved
the same (playlist owned by kids, admin's same-named playlist separate).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.plex_client as pc
from core.plex_client import PlexClient, PlexUserView


class _FakeHomeUser:
    def __init__(self, id, title, home=True, protected=False, pin=None):
        self.id, self.title, self.home, self.protected, self.restricted = id, title, home, protected, True
        self._pin = pin


class _FakeResource:
    def __init__(self, machine, token, provides='server'):
        self.clientIdentifier, self.accessToken, self.provides, self.name = machine, token, provides, 'srv'


class _FakeAccount:
    """MyPlexAccount: users(), switchHomeUser(), resources()"""
    machine = 'MACHINE-1'

    def __init__(self, users):
        self._users = users
        self.switched_to = []

    def users(self):
        return list(self._users)

    def switchHomeUser(self, user, pin=None):
        if user.protected and pin != user._pin:
            raise pc.PlexApiException('(401) unauthorized')
        self.switched_to.append((user.title, pin))
        acct = _FakeAccount(self._users)
        acct._resources = [_FakeResource(self.machine, f"srvtoken-{user.title}"),
                           _FakeResource('OTHER', 'x', provides='player')]
        return acct

    def resources(self):
        return self._resources


class _FakeSection:
    def __init__(self, title):
        self.title, self.type = title, 'artist'


class _FakePlexServer:
    """PlexServer(url, token): per-token playlists, like plex"""
    store = {}

    def __init__(self, url, token, timeout=None):
        self.url, self.token = url, token
        self.machineIdentifier = _FakeAccount.machine
        self.library = SimpleNamespace(sections=lambda: [_FakeSection('Music'), _FakeSection('Kids Music')])
        self.store.setdefault(token, {})

    @staticmethod
    def _pl(name):
        return SimpleNamespace(title=name, playlistType='audio', ratingKey=f"pl-{name}", summary='',
                               duration=0, leafCount=0, items=lambda: [])

    def playlists(self):
        return [self._pl(n) for n in self.store[self.token]]

    def playlist(self, name):
        if name not in self.store[self.token]:
            raise pc.NotFound(name)
        return self._pl(name)


@pytest.fixture()
def plex(monkeypatch):
    _FakePlexServer.store.clear()
    users = [_FakeHomeUser('1', 'Kids'), _FakeHomeUser('2', 'Annie', protected=True, pin='1234'),
             _FakeHomeUser('3', 'Friend', home=False)]
    account = _FakeAccount(users)
    monkeypatch.setattr(pc, 'PlexServer', _FakePlexServer)
    monkeypatch.setattr(pc.config_manager, 'get_plex_config', lambda: {'base_url': 'http://plex', 'token': 'admintoken'})
    client = PlexClient()
    monkeypatch.setattr(client, '_account', lambda: account)
    client.server = _FakePlexServer('http://plex', 'admintoken')
    client.music_library = _FakeSection('Music')
    client._connection_attempted = True
    return SimpleNamespace(client=client, account=account)


# ── listing and minting ────────────────────────────────────────────────────

def test_list_home_users_is_names_ids_and_pin_flag_only(plex):
    users = plex.client.list_home_users()
    assert users == [{'id': '1', 'title': 'Kids', 'protected': False, 'restricted': True},
                     {'id': '2', 'title': 'Annie', 'protected': True, 'restricted': True}]   # Friend is not Home


def test_minting_a_token_for_a_user_without_a_pin(plex):
    token, title, err = plex.client.mint_home_user_server_token('1')
    assert (token, title, err) == ('srvtoken-Kids', 'Kids', None)
    assert plex.account.switched_to == [('Kids', None)]


def test_a_protected_user_needs_the_pin_and_a_wrong_one_is_refused(plex):
    token, title, err = plex.client.mint_home_user_server_token('2')
    assert token is None and err == "That Plex user has a PIN; enter it to link"
    assert plex.account.switched_to == [], "a pin-less link attempt must not go to plex.tv at all"
    token, title, err = plex.client.mint_home_user_server_token('2', '0000')
    assert token is None and 'refused' in err
    token, title, err = plex.client.mint_home_user_server_token('2', '1234')
    assert token == 'srvtoken-Annie' and title == 'Annie'


def test_unknown_or_non_home_users_cannot_be_linked(plex):
    assert plex.client.mint_home_user_server_token('999')[2]
    assert plex.client.mint_home_user_server_token('3')[2]        # a shared friend, not Home


def test_a_user_who_cannot_see_this_server_is_refused(plex):
    plex.client.server.machineIdentifier = 'SOMEWHERE-ELSE'
    token, title, err = plex.client.mint_home_user_server_token('1')
    assert token is None and 'cannot see this server' in err


# ── the view ────────────────────────────────────────────────────────────────

def test_the_view_is_its_own_connection_as_the_user(plex):
    view = plex.client.as_home_user('srvtoken-Kids', 'Kids')
    assert isinstance(view, PlexUserView) and isinstance(view, PlexClient)
    assert view.server is not plex.client.server and view.server.token == 'srvtoken-Kids'
    assert view.acting_as == 'Kids' and view.ensure_connection() is True
    assert view.music_library.title == 'Music'          # starts on the app account's library
    # the shared client is untouched
    assert plex.client.server.token == 'admintoken' and plex.client.music_library.title == 'Music'


def test_the_users_connection_is_made_once_and_reused(plex):
    a = plex.client.as_home_user('srvtoken-Kids', 'Kids')
    b = plex.client.as_home_user('srvtoken-Kids', 'Kids')
    assert a.server is b.server
    assert plex.client.as_home_user('srvtoken-Annie', 'Annie').server is not a.server


def test_playlists_are_per_user_on_the_view(plex):
    view = plex.client.as_home_user('srvtoken-Kids', 'Kids')
    _FakePlexServer.store['srvtoken-Kids']['Chill'] = True
    _FakePlexServer.store['admintoken']['Mine'] = True
    assert view.get_playlist_by_name('Chill') is not None and view.get_playlist_by_name('Mine') is None
    assert plex.client.get_playlist_by_name('Mine') is not None and plex.client.get_playlist_by_name('Chill') is None


def test_a_library_pick_on_the_view_does_not_write_the_app_wide_preference(plex, monkeypatch):
    writes = []
    import database.music_database as mdb
    monkeypatch.setattr(mdb, 'MusicDatabase', lambda *a, **k: SimpleNamespace(set_preference=lambda k, v: writes.append((k, v))))
    view = plex.client.as_home_user('srvtoken-Kids', 'Kids')
    assert view.set_music_library_by_name('Kids Music') is True
    assert view.music_library.title == 'Kids Music'
    assert plex.client.music_library.title == 'Music'
    assert writes == []
    assert view.set_music_library_by_name('Nope') is False


def test_a_dead_token_gives_no_view(plex, monkeypatch):
    def boom(url, token, timeout=None):
        raise pc.PlexApiException('(401) unauthorized')
    monkeypatch.setattr(pc, 'PlexServer', boom)
    assert plex.client.as_home_user('revoked', 'Kids') is None


# ── the db ──────────────────────────────────────────────────────────────────

def test_profile_link_roundtrip_is_encrypted_and_clearable(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    pid = db.create_profile(name='kid')
    assert db.get_profile_plex_home_user(pid) is None
    assert db.set_profile_plex_home_user(pid, '1', 'Kids', 'srvtoken-Kids')
    assert db.get_profile_plex_home_user(pid) == {'id': '1', 'title': 'Kids', 'token': 'srvtoken-Kids'}
    with db._get_connection() as conn:
        raw = conn.execute("SELECT plex_home_user_token FROM profiles WHERE id = ?", (pid,)).fetchone()[0]
    assert raw != 'srvtoken-Kids' and raw.startswith('gAAAAA')
    libs = db.get_profile_server_library(pid)
    assert (libs['plex_home_user_id'], libs['plex_home_user_title']) == ('1', 'Kids')
    assert 'srvtoken' not in str(libs)
    assert db.set_profile_plex_home_user(pid, None, None, None)
    assert db.get_profile_plex_home_user(pid) is None
    assert db.get_profile_server_library(pid)['plex_home_user_id'] is None


def test_a_link_without_a_token_is_refused(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    pid = db.create_profile(name='kid')
    assert db.set_profile_plex_home_user(pid, '1', 'Kids', '') is False
    assert db.get_profile_plex_home_user(pid) is None


# ── the sync ────────────────────────────────────────────────────────────────

def _sync_service(monkeypatch, client, links):
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc._media_client = lambda name: client if name == 'plex' else None
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')

    class _DB:
        def get_profile_plex_home_user(self, pid):
            return links.get(pid)

        def get_profile_server_library(self, pid):
            return {}
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    return svc


def test_sync_connects_as_the_profiles_user_when_linked(plex, monkeypatch):
    svc = _sync_service(monkeypatch, plex.client, {2: {'id': '1', 'title': 'Kids', 'token': 'srvtoken-Kids'}})
    client, server = svc._get_active_media_client(profile_id=2)
    assert server == 'plex' and isinstance(client, PlexUserView) and client.acting_as == 'Kids'
    assert client.server.token == 'srvtoken-Kids'


def test_sync_stays_on_the_app_account_without_a_link(plex, monkeypatch):
    svc = _sync_service(monkeypatch, plex.client, {})
    assert svc._get_active_media_client(profile_id=2)[0] is plex.client
    assert svc._get_active_media_client(profile_id=None)[0] is plex.client


def test_a_link_whose_connection_fails_falls_back_to_the_app_account(plex, monkeypatch):
    svc = _sync_service(monkeypatch, plex.client, {2: {'id': '1', 'title': 'Kids', 'token': 'revoked'}})

    def boom(url, token, timeout=None):
        raise pc.PlexApiException('(401) unauthorized')
    monkeypatch.setattr(pc, 'PlexServer', boom)
    assert svc._get_active_media_client(profile_id=2)[0] is plex.client


@pytest.mark.parametrize('linked', [False, True])
def test_the_profiles_library_pick_lands_on_the_view(plex, monkeypatch, linked):
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc._media_client = lambda name: plex.client
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')

    class _DB:
        def get_profile_plex_home_user(self, pid):
            return {'id': '1', 'title': 'Kids', 'token': 'srvtoken-Kids'} if linked else None

        def get_profile_server_library(self, pid):
            return {'plex_library_id': 'Kids Music'}

        def set_preference(self, k, v):
            raise AssertionError('a profile pick must not write the app-wide preference')
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    client, _ = svc._get_active_media_client(profile_id=2)
    assert client.music_library.title == 'Kids Music'
    assert plex.client.music_library.title == 'Music'
