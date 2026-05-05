"""Tests for MediaServerEngine cross-server dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.media_server import MediaServerEngine, MediaServerRegistry
from core.media_server.registry import ServerSpec


class _FakeClient:
    """Stand-in client supporting all required + optional methods.
    Tests selectively override per-test to assert dispatch behavior."""

    def __init__(self, name='fake', connected=True):
        self.name = name
        self._connected = connected
        self._artists = []
        self._album_ids = set()
        self._search_results = []
        self._scan_triggered = False

    def is_connected(self):
        return self._connected

    def ensure_connection(self):
        return self._connected

    def get_all_artists(self):
        return self._artists

    def get_all_album_ids(self):
        return self._album_ids

    def search_tracks(self, title, artist, limit=15):
        return self._search_results

    def trigger_library_scan(self):
        self._scan_triggered = True
        return True

    def is_library_scanning(self):
        return False

    def get_library_stats(self):
        return {'artists': 0, 'albums': 0, 'tracks': 0}

    def get_recently_added_albums(self, max_results=400):
        return []


@pytest.fixture
def make_engine():
    """Build an engine with mock clients. Test passes a dict of
    name → client; engine wires them via a registry + custom
    active-server resolver."""

    def _make(clients_by_name, active='plex'):
        registry = MediaServerRegistry()
        for name, client in clients_by_name.items():
            registry.register(ServerSpec(
                name=name,
                factory=lambda c=client: c,
                display_name=name.title(),
            ))
        return MediaServerEngine(
            registry=registry,
            active_server_resolver=lambda: active,
        )

    return _make


# ---------------------------------------------------------------------------
# Active-server resolution
# ---------------------------------------------------------------------------


def test_active_server_property_reflects_resolver(make_engine):
    plex = _FakeClient('plex')
    jelly = _FakeClient('jellyfin')
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='jellyfin')
    assert engine.active_server == 'jellyfin'
    assert engine.active_client() is jelly


def test_client_lookup_by_name(make_engine):
    plex = _FakeClient('plex')
    engine = make_engine({'plex': plex}, active='plex')
    assert engine.client('plex') is plex
    assert engine.client('made_up') is None


# ---------------------------------------------------------------------------
# Required-method dispatch
# ---------------------------------------------------------------------------


def test_is_connected_routes_to_active_client(make_engine):
    plex = _FakeClient('plex', connected=True)
    jelly = _FakeClient('jellyfin', connected=False)
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='jellyfin')
    assert engine.is_connected() is False  # follows jellyfin
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='plex')
    assert engine.is_connected() is True  # follows plex


def test_get_all_album_ids_returns_active_clients_set(make_engine):
    plex = _FakeClient('plex')
    plex._album_ids = {'p-1', 'p-2'}
    jelly = _FakeClient('jellyfin')
    jelly._album_ids = {'j-1'}
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='plex')
    assert engine.get_all_album_ids() == {'p-1', 'p-2'}
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='jellyfin')
    assert engine.get_all_album_ids() == {'j-1'}


def test_engine_returns_safe_defaults_when_active_client_failed_to_init(make_engine):
    """When the active client failed to initialize (registry stored
    None), the engine returns safe defaults instead of raising."""
    registry = MediaServerRegistry()
    registry.register(ServerSpec(
        name='broken',
        factory=lambda: (_ for _ in ()).throw(RuntimeError("init failed")),
        display_name='Broken',
    ))
    registry.initialize()  # captures the exception
    engine = MediaServerEngine(registry=registry, active_server_resolver=lambda: 'broken')

    assert engine.is_connected() is False
    assert engine.get_all_artists() == []
    assert engine.get_all_album_ids() == set()
    assert engine.search_tracks('t', 'a') == []
    assert engine.trigger_library_scan() is False
    assert engine.is_library_scanning() is False


def test_engine_swallows_per_method_exceptions(make_engine):
    """A method that raises must NOT propagate to the dispatch
    site — engine returns the safe default instead, mirroring the
    legacy web_server.py defensive try/except chains."""
    plex = _FakeClient('plex')
    plex.is_connected = MagicMock(side_effect=RuntimeError("boom"))
    plex.get_all_album_ids = MagicMock(side_effect=RuntimeError("boom"))
    engine = make_engine({'plex': plex}, active='plex')

    assert engine.is_connected() is False
    assert engine.get_all_album_ids() == set()


# ---------------------------------------------------------------------------
# Optional-method dispatch (engine returns safe default when missing)
# ---------------------------------------------------------------------------


class _MinimalClient:
    """Stand-in for SoulSync standalone — only the required methods,
    NO optional methods. Used to assert engine routes around missing
    optional methods with safe defaults."""

    def is_connected(self): return True
    def ensure_connection(self): return True
    def get_all_artists(self): return []
    def get_all_album_ids(self): return set()


def test_search_tracks_returns_empty_when_client_lacks_method(make_engine):
    """SoulSync standalone has no search_tracks — engine returns
    [] instead of raising AttributeError."""
    engine = make_engine({'soulsync': _MinimalClient()}, active='soulsync')
    assert engine.search_tracks('t', 'a') == []


def test_trigger_library_scan_returns_true_when_client_lacks_method(make_engine):
    """SoulSync has no trigger_library_scan (filesystem walks
    happen in-process). Engine no-ops with True so callers don't
    treat it as a failure."""
    engine = make_engine({'soulsync': _MinimalClient()}, active='soulsync')
    assert engine.trigger_library_scan() is True


def test_is_library_scanning_returns_false_when_client_lacks_method(make_engine):
    engine = make_engine({'soulsync': _MinimalClient()}, active='soulsync')
    assert engine.is_library_scanning() is False


def test_get_library_stats_returns_empty_dict_when_client_lacks_method(make_engine):
    engine = make_engine({'soulsync': _MinimalClient()}, active='soulsync')
    assert engine.get_library_stats() == {}
