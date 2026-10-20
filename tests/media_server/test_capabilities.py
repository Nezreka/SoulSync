"""Declared capabilities are the replacement for ``active_server == 'x'``
at call sites, so the declarations have to stay honest: every capability
a client declares must have its backing methods, and the known gaps
(Jellyfin has no track search, Navidrome's metadata writers are stubs,
the standalone library has no playlists) must stay undeclared until the
code behind them exists."""

from __future__ import annotations

import pytest

from core.media_server.base import MediaServerBase
from core.media_server.contract import ALL_CAPABILITIES, CAPABILITY_METHODS, Capability
from core.media_server.engine import MediaServerEngine
from core.media_server.registry import MediaServerRegistry, ServerSpec

SERVERS = ['plex', 'jellyfin', 'navidrome', 'soulsync']


def _classes():
    from core.jellyfin_client import JellyfinClient
    from core.navidrome_client import NavidromeClient
    from core.plex_client import PlexClient
    from core.soulsync_client import SoulSyncClient
    return {'plex': PlexClient, 'jellyfin': JellyfinClient,
            'navidrome': NavidromeClient, 'soulsync': SoulSyncClient}


@pytest.mark.parametrize('server', SERVERS)
def test_client_inherits_the_shared_base_and_declares_capabilities(server):
    cls = _classes()[server]
    assert issubclass(cls, MediaServerBase)
    assert cls.CAPABILITIES, f'{server} declares nothing'
    unknown = cls.CAPABILITIES - ALL_CAPABILITIES
    assert not unknown, f'{server} declares capabilities the contract does not know: {unknown}'


@pytest.mark.parametrize('server', SERVERS)
def test_every_declared_capability_has_its_methods(server):
    cls = _classes()[server]
    assert cls.declared_capability_gaps() == {}, (
        f'{server} declares capabilities without the methods behind them'
    )


def test_known_gaps_stay_undeclared():
    c = _classes()
    assert Capability.SEARCH not in c['jellyfin'].CAPABILITIES
    assert Capability.METADATA_WRITE not in c['navidrome'].CAPABILITIES, 'stubs that log "not implemented"'
    assert Capability.TRACK_METADATA_WRITE not in c['jellyfin'].CAPABILITIES
    assert Capability.PLAYLISTS not in c['soulsync'].CAPABILITIES
    assert Capability.PER_USER_VIEWS not in c['soulsync'].CAPABILITIES


def test_tag_write_followup_is_one_of_three_declared_shapes():
    """_sync_tracks_to_server branches Plex / Jellyfin / Navidrome by name;
    the three branches mean push metadata / request a rescan / nothing.
    Every server declares exactly one of those, so the branch can become
    a capability check."""
    for server, cls in _classes().items():
        caps = cls.CAPABILITIES
        shapes = [Capability.TRACK_METADATA_WRITE in caps,
                  Capability.TAGS_AUTODETECTED in caps]
        if not any(shapes):
            assert Capability.SCAN_TRIGGER in caps, server
        assert sum(shapes) <= 1, f'{server} both pushes metadata and autodetects tags'


def test_capability_methods_cover_the_vocabulary():
    assert set(CAPABILITY_METHODS) == ALL_CAPABILITIES
    assert CAPABILITY_METHODS[Capability.TAGS_AUTODETECTED] == ()


class _Fake(MediaServerBase):
    def __init__(self, caps):
        self.CAPABILITIES = frozenset(caps)

    def is_connected(self):
        return True

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return []

    def get_all_album_ids(self):
        return set()


def _engine(clients, active):
    registry = MediaServerRegistry()
    for name, client in clients.items():
        registry.register(ServerSpec(name=name, factory=lambda c=client: c, display_name=name))
    return MediaServerEngine(registry=registry, active_server_resolver=lambda: active, clients=clients)


def test_engine_capability_queries():
    a = _Fake({Capability.SEARCH, Capability.PLAYLISTS})
    b = _Fake({Capability.PLAYLISTS})
    engine = _engine({'a': a, 'b': b}, active='a')
    assert engine.supports('a', Capability.SEARCH)
    assert not engine.supports('b', Capability.SEARCH)
    assert not engine.supports('missing', Capability.SEARCH)
    assert engine.active_supports(Capability.SEARCH)
    assert set(engine.clients_with(Capability.PLAYLISTS)) == {'a', 'b'}
    assert a.supports(Capability.SEARCH) and not b.supports(Capability.SEARCH)


def test_hook_defaults():
    f = _Fake(set())
    assert f.scan_hints() == {}
    assert f.post_scan(None) is True
    assert f.user_view({'token': 'x'}) is None


def test_user_view_dispatches_to_each_servers_acting_as_method(monkeypatch):
    c = _classes()
    seen = {}
    monkeypatch.setattr(c['plex'], 'as_home_user', lambda self, token, title='': seen.setdefault('plex', (token, title)))
    monkeypatch.setattr(c['jellyfin'], 'as_user', lambda self, user_id, library_id=None: seen.setdefault('jellyfin', (user_id, library_id)))
    monkeypatch.setattr(c['navidrome'], 'as_user', lambda self, username, password: seen.setdefault('navidrome', (username, password)))
    plex = c['plex'].__new__(c['plex'])
    jf = c['jellyfin'].__new__(c['jellyfin'])
    nd = c['navidrome'].__new__(c['navidrome'])
    plex.user_view({'token': 't', 'title': 'Kid'})
    jf.user_view({'user_id': 'u1', 'library_id': 'l1'})
    nd.user_view({'username': 'n', 'password': 'p'})
    assert seen == {'plex': ('t', 'Kid'), 'jellyfin': ('u1', 'l1'), 'navidrome': ('n', 'p')}
    assert plex.user_view({}) is None and jf.user_view(None) is None and nd.user_view({'username': 'n'}) is None


def test_scan_hints_match_what_the_worker_hard_codes():
    c = _classes()
    jf = c['jellyfin'].__new__(c['jellyfin'])
    nd = c['navidrome'].__new__(c['navidrome'])
    assert jf.scan_hints()['max_workers_cap'] == 3
    assert 'preparing_message' in nd.scan_hints() and nd.scan_hints()['caches_listings'] is True
    assert c['plex'].__new__(c['plex']).scan_hints() == {}


def test_navidrome_post_scan_runs_the_identity_repair_as_one_call(monkeypatch):
    from core.library import navidrome_identity as ni
    c = _classes()['navidrome'].__new__(_classes()['navidrome'])
    seen = {}
    monkeypatch.setattr(ni, 'has_same_path_duplicates', lambda db: True)
    monkeypatch.setattr(ni, 'read_inventory', lambda client: {'inv': client})
    monkeypatch.setattr(ni, 'repair_rekeyed_tracks', lambda db, songs: seen.setdefault('args', (db, songs)) and 2)
    assert c.post_scan('db') is True
    assert seen['args'] == ('db', {'inv': c})
    monkeypatch.setattr(ni, 'has_same_path_duplicates', lambda db: False)
    monkeypatch.setattr(ni, 'read_inventory', lambda client: (_ for _ in ()).throw(AssertionError('no fetch without duplicates')))
    assert c.post_scan('db') is True
    assert c.post_scan(None) is False
