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

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core import metadata_service
from config.settings import config_manager


@pytest.fixture(autouse=True)
def _clear_metadata_client_cache():
    metadata_service.clear_cached_metadata_clients()
    yield
    metadata_service.clear_cached_metadata_clients()


def test_primary_client_is_cached_for_same_source(monkeypatch):
    calls = {"deezer": 0}

    class FakeDeezerClient:
        def __init__(self):
            calls["deezer"] += 1

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr("core.deezer_client.DeezerClient", FakeDeezerClient)

    first = metadata_service.get_primary_client()
    second = metadata_service.get_primary_client()

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

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: next(sources))
    monkeypatch.setattr("core.deezer_client.DeezerClient", FakeDeezerClient)
    monkeypatch.setattr(metadata_service, "iTunesClient", FakeITunesClient)

    deezer_client = metadata_service.get_primary_client()
    itunes_client = metadata_service.get_primary_client()

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

    first = metadata_service.get_deezer_client()
    second = metadata_service.get_deezer_client()

    assert first is not second
    assert calls["deezer"] == 2


class _FakeHydrabaseClient:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected


def test_hydrabase_enabled_requires_connection_and_dev_mode(monkeypatch):
    fake_ws = types.ModuleType("web_server")
    fake_ws.hydrabase_client = _FakeHydrabaseClient(connected=True)
    fake_ws.dev_mode_enabled = True
    monkeypatch.setitem(sys.modules, "web_server", fake_ws)

    assert metadata_service.is_hydrabase_enabled() is True

    fake_ws.dev_mode_enabled = False
    assert metadata_service.is_hydrabase_enabled() is False

    fake_ws.dev_mode_enabled = True
    fake_ws.hydrabase_client = _FakeHydrabaseClient(connected=False)
    assert metadata_service.is_hydrabase_enabled() is False


def test_get_client_for_source_hydrabase_requires_enablement(monkeypatch):
    fake_ws = types.ModuleType("web_server")
    fake_ws.hydrabase_client = _FakeHydrabaseClient(connected=True)
    fake_ws.dev_mode_enabled = False
    monkeypatch.setitem(sys.modules, "web_server", fake_ws)

    assert metadata_service.get_client_for_source("hydrabase") is None

    fake_ws.dev_mode_enabled = True
    assert metadata_service.get_client_for_source("hydrabase") is fake_ws.hydrabase_client
