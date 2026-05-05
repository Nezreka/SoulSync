"""Tests for MediaServerEngine cross-server dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.media_server.engine import MediaServerEngine
from core.media_server.registry import MediaServerRegistry, ServerSpec


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


# ---------------------------------------------------------------------------
# Generic accessors (Cin's standard from the download refactor)
# ---------------------------------------------------------------------------


def test_configured_clients_only_returns_connected_servers(make_engine):
    """Replaces the legacy per-server `if X and X.is_connected(): ...`
    chains in web_server.py. Single call returns the dict."""
    plex = _FakeClient('plex', connected=True)
    jelly = _FakeClient('jellyfin', connected=False)
    soulsync = _FakeClient('soulsync', connected=True)
    engine = make_engine(
        {'plex': plex, 'jellyfin': jelly, 'soulsync': soulsync},
        active='plex',
    )
    result = engine.configured_clients()
    assert set(result.keys()) == {'plex', 'soulsync'}
    assert result['plex'] is plex
    assert result['soulsync'] is soulsync


def test_configured_clients_skips_clients_whose_is_connected_raises(make_engine):
    """Defensive: a single broken is_connected() must not crash the
    iteration. Healthy clients still come back."""
    healthy = _FakeClient('plex', connected=True)
    broken = _FakeClient('jellyfin')
    broken.is_connected = MagicMock(side_effect=RuntimeError("boom"))
    engine = make_engine({'plex': healthy, 'jellyfin': broken}, active='plex')
    result = engine.configured_clients()
    assert 'plex' in result
    assert 'jellyfin' not in result


def test_reload_config_dispatches_to_named_server(make_engine):
    """Generic dispatch — caller passes server name instead of
    reaching for plex_client.reload_config() directly."""

    class _ReloadablePlex(_FakeClient):
        def __init__(self):
            super().__init__('plex')
            self.reload_called = False

        def reload_config(self):
            self.reload_called = True

    plex = _ReloadablePlex()
    soulsync = _FakeClient('soulsync')  # No reload_config method
    engine = make_engine({'plex': plex, 'soulsync': soulsync}, active='plex')

    assert engine.reload_config('plex') is True
    assert plex.reload_called is True


def test_reload_config_skips_clients_without_method(make_engine):
    """Servers that don't expose reload_config are skipped silently
    (return True)."""
    soulsync = _FakeClient('soulsync')
    engine = make_engine({'soulsync': soulsync}, active='soulsync')
    assert engine.reload_config('soulsync') is True


def test_reload_config_with_no_args_reloads_every_server(make_engine):
    """When called with no name, hits every registered server that
    exposes reload_config."""

    class _ReloadableClient(_FakeClient):
        def __init__(self, name):
            super().__init__(name)
            self.reload_called = False

        def reload_config(self):
            self.reload_called = True

    plex = _ReloadableClient('plex')
    jelly = _ReloadableClient('jellyfin')
    engine = make_engine({'plex': plex, 'jellyfin': jelly}, active='plex')

    engine.reload_config()
    assert plex.reload_called is True
    assert jelly.reload_called is True


# ---------------------------------------------------------------------------
# Singleton factory (matches get_metadata_engine() / get_download_orchestrator())
# ---------------------------------------------------------------------------


def test_get_media_server_engine_returns_set_singleton(make_engine):
    """When set_media_server_engine has been called (web_server.py
    does this at boot), get_media_server_engine returns the installed
    instance instead of building a fresh one with the default registry."""
    from core.media_server.engine import (
        get_media_server_engine,
        set_media_server_engine,
    )

    engine = make_engine({'plex': _FakeClient('plex')}, active='plex')
    set_media_server_engine(engine)
    try:
        assert get_media_server_engine() is engine
    finally:
        set_media_server_engine(None)
