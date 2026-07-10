import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.metadata import registry


def test_spotify_disconnect_source_uses_deezer_when_spotify_is_primary():
    assert registry.get_spotify_disconnect_source("spotify") == "deezer"


def test_spotify_disconnect_source_keeps_non_spotify_primary():
    assert registry.get_spotify_disconnect_source("discogs") == "discogs"


def test_metadata_source_label_maps_known_sources():
    assert registry.get_metadata_source_label("spotify") == "Spotify"
    assert registry.get_metadata_source_label("itunes") == "iTunes"
    assert registry.get_metadata_source_label("deezer") == "Deezer"
    assert registry.get_metadata_source_label("discogs") == "Discogs"
    assert registry.get_metadata_source_label("hydrabase") == "Hydrabase"
    assert registry.get_metadata_source_label("musicbrainz") == "MusicBrainz"
    assert registry.get_metadata_source_label("jiosaavn") == "JioSaavn"


def test_musicbrainz_is_first_class_metadata_client():
    registry.clear_cached_metadata_clients()
    client = object()
    assert registry.get_client_for_source(
        "musicbrainz",
        musicbrainz_client_factory=lambda: client,
    ) is client


def test_experimental_source_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_get_config_value",
        lambda key, default=None: default,
    )
    # Non-experimental sources are always enabled and never rejected.
    assert registry.is_experimental_source("jiosaavn") is True
    assert registry.is_experimental_source("deezer") is False
    assert registry.is_source_enabled("deezer") is True
    assert registry.is_source_enabled("jiosaavn") is False
    assert registry.is_jiosaavn_enabled() is False
    assert registry.experimental_source_rejected("jiosaavn") is True
    assert registry.experimental_source_rejected("deezer") is False
    assert registry.experimental_source_rejected("") is False
    assert registry.experimental_status() == {"jiosaavn_enabled": False, "bandcamp_enabled": False}


def test_experimental_source_not_rejected_when_enabled(monkeypatch):
    monkeypatch.setattr(registry, "is_source_enabled", lambda source: True)
    assert registry.experimental_source_rejected("jiosaavn") is False


def test_primary_metadata_source_rejection_error():
    assert registry.primary_metadata_source_rejection_error("deezer") is None
    assert registry.primary_metadata_source_rejection_error("jiosaavn") is not None
    assert "Experimental" in registry.primary_metadata_source_rejection_error("jiosaavn")


def test_apply_primary_metadata_source_spotify_free_composite():
    stored = {}

    def _set(key, value):
        stored[key] = value

    assert registry.apply_primary_metadata_source("spotify_free", _set) is None
    assert stored == {
        "metadata.fallback_source": "spotify",
        "metadata.spotify_free": True,
    }


def test_apply_primary_metadata_source_rejects_disabled_experimental(monkeypatch):
    monkeypatch.setattr(registry, "is_source_enabled", lambda source: False)
    stored = {}

    err = registry.apply_primary_metadata_source("jiosaavn", stored.__setitem__)
    assert err is not None
    assert not stored


def test_resolve_settings_metadata_primary_enable_and_select():
    err, override = registry.resolve_settings_metadata_primary(
        {"jiosaavn_enabled": True},
        {"fallback_source": "jiosaavn"},
        lambda _key: False,
    )
    assert err is None
    assert override is None


def test_resolve_settings_metadata_primary_rejects_explicit_without_enable():
    err, override = registry.resolve_settings_metadata_primary(
        {"jiosaavn_enabled": False},
        {"fallback_source": "jiosaavn"},
        lambda _key: False,
    )
    assert err is not None
    assert override is None


def test_resolve_settings_metadata_primary_resets_when_disabling_experimental():
    err, override = registry.resolve_settings_metadata_primary(
        {"jiosaavn_enabled": False},
        None,
        lambda key: "jiosaavn" if key == "metadata.fallback_source" else True,
    )
    assert err is None
    assert override == "deezer"


def test_experimental_source_is_first_class_metadata_client(monkeypatch):
    monkeypatch.setattr(registry, "is_source_enabled", lambda source: True)
    registry.clear_cached_metadata_clients()
    client = object()
    assert registry.get_client_for_source(
        "jiosaavn",
        jiosaavn_client_factory=lambda: client,
    ) is client


def test_experimental_client_gated_when_disabled(monkeypatch):
    monkeypatch.setattr(registry, "is_source_enabled", lambda source: False)
    registry.clear_cached_metadata_clients()
    client = object()
    assert registry.get_client_for_source(
        "jiosaavn",
        jiosaavn_client_factory=lambda: client,
    ) is None


def test_primary_source_downgrades_disabled_experimental(monkeypatch):
    monkeypatch.setattr(registry, "is_source_enabled", lambda source: source != "jiosaavn")
    monkeypatch.setattr(registry, "get_configured_primary_source", lambda: "jiosaavn")
    assert registry.get_primary_source() == "deezer"


def test_metadata_source_label_falls_back_to_unmapped():
    assert registry.get_metadata_source_label("apple_music") == "Unmapped"
