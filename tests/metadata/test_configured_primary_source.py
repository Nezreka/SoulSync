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
    """Spotify that can serve NOTHING — no auth, no free source — still downgrades.

    ``is_spotify_metadata_available`` has to be stubbed explicitly: a bare
    MagicMock auto-creates every attribute and returns a truthy Mock, so it
    would silently claim the no-creds source is available and this would assert
    the opposite of what it reads like. The downgrade under test is for a
    Spotify that genuinely cannot serve metadata, which is what these two
    stubs together describe.
    """
    monkeypatch.setattr(registry, "get_configured_primary_source", lambda: "spotify")

    spotify = MagicMock()
    spotify.is_spotify_authenticated.return_value = False
    spotify.is_spotify_metadata_available.return_value = False
    monkeypatch.setattr(registry, "get_spotify_client", lambda **_: spotify)

    assert registry.get_primary_source() == registry.METADATA_SOURCE_PRIORITY[0]


def test_get_primary_source_keeps_spotify_when_only_the_free_source_is_available(monkeypatch):
    """The companion case, pinned here too because this file is where the
    downgrade contract lives: a Spotify Free user is unauthenticated forever by
    design, so gating on auth alone silently demoted their configured source on
    every call. See tests/test_spotify_free_status.py for the full set.
    """
    monkeypatch.setattr(registry, "get_configured_primary_source", lambda: "spotify")

    spotify = MagicMock()
    spotify.is_spotify_authenticated.return_value = False
    spotify.is_spotify_metadata_available.return_value = True
    monkeypatch.setattr(registry, "get_spotify_client", lambda **_: spotify)

    assert registry.get_primary_source() == "spotify"
