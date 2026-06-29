"""Tests for boot-safe configured primary source lookup."""

from unittest.mock import MagicMock, patch

from core.boot_phase import mark_boot_complete
from core.metadata import registry


def setup_function():
    mark_boot_complete()


def test_get_configured_primary_source_reads_config_without_auth_probe(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_get_config_value",
        lambda key, default=None: "spotify" if key == "metadata.fallback_source" else default,
    )

    with patch.object(registry, "get_spotify_client") as get_client:
        assert registry.get_configured_primary_source() == "spotify"
        get_client.assert_not_called()


def test_get_primary_source_still_downgrades_unauthenticated_spotify(monkeypatch):
    monkeypatch.setattr(registry, "get_configured_primary_source", lambda: "spotify")

    spotify = MagicMock()
    spotify.is_spotify_authenticated.return_value = False
    monkeypatch.setattr(registry, "get_spotify_client", lambda **_: spotify)

    assert registry.get_primary_source() == registry.METADATA_SOURCE_PRIORITY[0]
