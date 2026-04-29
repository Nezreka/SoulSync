import sys
import types

import pytest


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.metadata import registry as metadata_registry
from config.settings import config_manager


@pytest.fixture(autouse=True)
def _clear_metadata_client_cache():
    metadata_registry.clear_cached_metadata_clients()
    yield
    metadata_registry.clear_cached_metadata_clients()


def test_primary_client_is_cached_for_same_source(monkeypatch):
    calls = {"deezer": 0}

    class FakeDeezerClient:
        def __init__(self):
            calls["deezer"] += 1

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr("core.deezer_client.DeezerClient", FakeDeezerClient)

    first = metadata_registry.get_primary_client()
    second = metadata_registry.get_primary_client()

    assert first is second
    assert calls["deezer"] == 1


def test_primary_client_switches_cache_by_source(monkeypatch):
    calls = {"deezer": 0, "itunes": 0}
    sources = iter(["deezer", "itunes"])

    class FakeDeezerClient:
        def __init__(self):
            calls["deezer"] += 1

    class FakeITunesClient:
        def __init__(self):
            calls["itunes"] += 1

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: next(sources))
    monkeypatch.setattr("core.deezer_client.DeezerClient", FakeDeezerClient)
    monkeypatch.setattr("core.itunes_client.iTunesClient", FakeITunesClient)

    deezer_client = metadata_registry.get_primary_client()
    itunes_client = metadata_registry.get_primary_client()

    assert deezer_client is not itunes_client
    assert calls["deezer"] == 1
    assert calls["itunes"] == 1


def test_deezer_client_cache_tracks_token(monkeypatch):
    tokens = iter(["token-a", "token-b"])
    calls = {"deezer": 0}

    class FakeDeezerClient:
        def __init__(self):
            calls["deezer"] += 1

    monkeypatch.setattr("core.deezer_client.DeezerClient", FakeDeezerClient)
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: next(tokens) if key == "deezer.access_token" else default)

    first = metadata_registry.get_deezer_client()
    second = metadata_registry.get_deezer_client()

    assert first is not second
    assert calls["deezer"] == 2


class _FakeHydrabaseClient:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected


def test_hydrabase_enabled_requires_connection_and_dev_mode(monkeypatch):
    metadata_registry.register_runtime_clients(
        hydrabase_client=_FakeHydrabaseClient(connected=True),
        dev_mode_enabled_provider=lambda: True,
    )

    assert metadata_registry.is_hydrabase_enabled() is True

    metadata_registry.register_runtime_clients(dev_mode_enabled_provider=lambda: False)
    assert metadata_registry.is_hydrabase_enabled() is False

    metadata_registry.register_runtime_clients(
        hydrabase_client=_FakeHydrabaseClient(connected=False),
        dev_mode_enabled_provider=lambda: True,
    )
    assert metadata_registry.is_hydrabase_enabled() is False


def test_get_client_for_source_hydrabase_requires_enablement(monkeypatch):
    metadata_registry.register_runtime_clients(
        hydrabase_client=_FakeHydrabaseClient(connected=True),
        dev_mode_enabled_provider=lambda: False,
    )

    assert metadata_registry.get_client_for_source("hydrabase") is None

    metadata_registry.register_runtime_clients(dev_mode_enabled_provider=lambda: True)
    assert metadata_registry.get_client_for_source("hydrabase") is metadata_registry.get_registered_runtime_client("hydrabase")
