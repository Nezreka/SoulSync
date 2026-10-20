"""One facade answers "who is this profile on that server", shaped for the
client's user_view hook, over the three per-server storages. The sync
service resolves a per-profile client through it instead of one function
per server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase as _RealDb


class _Db:
    def __init__(self, plex=None, jf=None, nd=None):
        self._plex, self._jf, self._nd = plex, jf, nd

    def get_profile_plex_home_user(self, pid):
        return self._plex

    def get_profile_server_library(self, pid):
        return self._jf or {}

    def get_profile_navidrome_login(self, pid):
        return self._nd

    def get_profile_server_identity(self, pid, server):
        return _facade(self, pid, server)


def _facade(db, pid, server):
    # bound at import so a test that patches the module attribute still
    # reaches the real method
    return _RealDb.get_profile_server_identity(db, pid, server)


def test_identity_shapes_match_each_servers_user_view():
    db = _Db(plex={'id': '7', 'title': 'Kid', 'token': 't'},
             jf={'jellyfin_user_id': 'u1', 'jellyfin_library_id': 'l1'},
             nd=('nick', 'pw'))
    assert _facade(db, 1, 'plex') == {'token': 't', 'title': 'Kid', 'id': '7', 'label': 'Kid'}
    assert _facade(db, 1, 'jellyfin') == {'user_id': 'u1', 'library_id': 'l1', 'label': 'u1'}
    assert _facade(db, 1, 'navidrome') == {'username': 'nick', 'password': 'pw', 'label': 'nick'}


def test_no_identity_means_the_app_account():
    db = _Db()
    for server in ('plex', 'jellyfin', 'navidrome', 'soulsync', 'emby'):
        assert _facade(db, 1, server) is None
    assert _facade(db, None, 'plex') is None


def test_client_for_profile_uses_user_view(monkeypatch):
    from services import sync_service

    class _Client:
        def __init__(self):
            self.seen = None

        def user_view(self, identity):
            self.seen = identity
            return 'view'

    db = _Db(nd=('nick', 'pw'))
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    client = _Client()
    assert sync_service.client_for_profile(1, client, 'navidrome') == 'view'
    assert client.seen['username'] == 'nick'
    # no identity, no hook, no profile: the client as-is
    assert sync_service.client_for_profile(1, client, 'plex') is client
    assert sync_service.client_for_profile(None, client, 'navidrome') is client
    assert sync_service.client_for_profile(1, object(), 'navidrome').__class__ is object


def test_client_for_profile_falls_back_when_the_view_cannot_connect(monkeypatch):
    from services import sync_service
    db = _Db(plex={'id': '7', 'title': 'Kid', 'token': 't'})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    client = SimpleNamespace(user_view=lambda identity: None)
    assert sync_service.client_for_profile(1, client, 'plex') is client


@pytest.mark.parametrize('server,expected', [
    ('plex', 'user:kid'), ('jellyfin', 'user:u1'), ('navidrome', 'user:nick'), ('soulsync', 'shared'),
])
def test_server_account_of_reads_the_facade(server, expected):
    from core.playlists import sync_names
    identities = {'plex': {'token': 't', 'title': 'Kid', 'label': 'Kid'},
                  'jellyfin': {'user_id': 'u1', 'library_id': None, 'label': 'u1'},
                  'navidrome': {'username': 'nick', 'password': 'pw', 'label': 'nick'}}
    db = SimpleNamespace(get_profile_server_identity=lambda pid, s: identities.get(s))
    out = sync_names.server_account_of(db, server, 1)
    assert out == expected if expected != 'shared' else out == sync_names.SHARED_ACCOUNT


def test_server_account_of_jellyfin_library_pick_alone_is_shared():
    from core.playlists import sync_names
    db = SimpleNamespace(get_profile_server_identity=lambda pid, s: {'user_id': None, 'library_id': 'l1', 'label': 'default'})
    assert sync_names.server_account_of(db, 'jellyfin', 1) == sync_names.SHARED_ACCOUNT
