"""Boot-phase guards must defer blocking provider network probes."""

from unittest.mock import MagicMock, patch

from core.boot_phase import is_boot_phase, mark_boot_complete
from core.metadata import registry


def setup_function():
    mark_boot_complete()


def teardown_function():
    mark_boot_complete()


def test_get_primary_source_skips_spotify_probe_during_boot(monkeypatch):
    import core.boot_phase as boot_phase

    boot_phase._boot_active = True
    monkeypatch.setattr(registry, "get_configured_primary_source", lambda: "spotify")

    with patch.object(registry, "get_spotify_client") as get_client:
        assert registry.get_primary_source() == "spotify"
        get_client.assert_not_called()


def test_get_primary_source_status_skips_client_probe_during_boot(monkeypatch):
    import core.boot_phase as boot_phase

    boot_phase._boot_active = True
    monkeypatch.setattr(
        registry, "_get_config_value",
        lambda key, default=None: "spotify" if key == "metadata.fallback_source" else default,
    )

    with patch.object(registry, "get_client_for_source") as get_client:
        status = registry.get_primary_source_status()
        get_client.assert_not_called()
        assert status["source"] == "spotify"
        assert status["connected"] is False


def test_spotify_auth_uses_token_presence_only_during_boot(monkeypatch):
    import core.boot_phase as boot_phase
    from core.spotify_client import SpotifyClient

    boot_phase._boot_active = True
    client = SpotifyClient.__new__(SpotifyClient)
    client.sp = MagicMock()
    client._auth_cache_lock = __import__('threading').Lock()
    client._auth_cached_result = None
    client._auth_cache_time = 0
    client._AUTH_CACHE_TTL = 900

    monkeypatch.setattr(client, "_has_cached_oauth_token", lambda: True)

    with patch("spotipy.Spotify") as spotify_cls:
        assert client.is_spotify_authenticated() is True
        spotify_cls.assert_not_called()


def test_deezer_download_defers_arl_auth_during_boot(monkeypatch):
    import core.boot_phase as boot_phase
    from core.deezer_download_client import DeezerDownloadClient

    boot_phase._boot_active = True
    monkeypatch.setattr(
        "config.settings.config_manager.get",
        lambda key, default=None: "fake-arl" if key == "deezer_download.arl" else default,
    )

    with patch.object(DeezerDownloadClient, "_authenticate") as authenticate:
        client = DeezerDownloadClient(download_path="/tmp/deezer-test")
        authenticate.assert_not_called()
        assert client._pending_arl == "fake-arl"
        assert client.is_authenticated() is False
