"""Seam tests for the per-source album-art lookups + availability.

Real clients are stubbed (monkeypatched at the lazy-import sites), so these
exercise the field-extraction, caching, guarding, and availability gating
without any network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.metadata import art_lookup


# ---------------------------------------------------------------------------
# Availability — "not everybody has every source"
# ---------------------------------------------------------------------------


def test_free_sources_always_available():
    for s in ("caa", "deezer", "itunes", "audiodb"):
        assert art_lookup.is_art_source_available(s) is True


def test_unknown_or_unsupported_source_unavailable():
    assert art_lookup.is_art_source_available("tidal") is False  # deferred
    assert art_lookup.is_art_source_available("genius") is False
    assert art_lookup.is_art_source_available("") is False


def test_spotify_availability_follows_connection(monkeypatch):
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: object())
    assert art_lookup.is_art_source_available("spotify") is True
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: None)
    assert art_lookup.is_art_source_available("spotify") is False


def test_spotify_availability_swallows_errors(monkeypatch):
    import core.metadata.registry as registry
    def _boom(_s):
        raise RuntimeError("registry down")
    monkeypatch.setattr(registry, "get_client_for_source", _boom)
    assert art_lookup.is_art_source_available("spotify") is False


def test_available_sources_lists_free_plus_connected_spotify(monkeypatch):
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: object())
    avail = art_lookup.available_art_sources()
    assert avail == ["caa", "deezer", "itunes", "spotify", "audiodb"]
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: None)
    assert "spotify" not in art_lookup.available_art_sources()


# ---------------------------------------------------------------------------
# Per-source extraction
# ---------------------------------------------------------------------------


def test_caa_art_builds_url_from_release_mbid():
    url = art_lookup._caa_art("A", "B", {"musicbrainz_release_id": "abc-123"})
    assert url == "https://coverartarchive.org/release/abc-123/front-1200"


def test_caa_art_none_without_mbid():
    assert art_lookup._caa_art("A", "B", {}) is None


def test_deezer_art_extracts_and_prefers_largest(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = {"title": "B", "artist": {"name": "A"},
                                        "cover_big": "http://x/big.jpg",
                                        "cover_xl": "http://x/xl.jpg"}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    # _upgrade_deezer_cover_url returns non-Deezer URLs unchanged.
    assert art_lookup._deezer_art("A", "B", {}) == "http://x/xl.jpg"


def test_deezer_art_none_when_no_cover_fields(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    # Matches the album but carries no cover_* keys.
    client.search_album.return_value = {"title": "B", "artist": {"name": "A"}, "id": 1}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    assert art_lookup._deezer_art("A", "B", {}) is None


def test_deezer_art_rejects_wrong_album(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = {"title": "A Totally Different Record",
                                        "artist": {"name": "A"},
                                        "cover_xl": "http://x/wrong.jpg"}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    # Wrong album -> no art (falls back to today's cover), never wrong art.
    assert art_lookup._deezer_art("A", "B", {}) is None


def test_deezer_art_rejects_wrong_artist(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = {"title": "21", "artist": {"name": "Someone Else"},
                                        "cover_xl": "http://x/wrong.jpg"}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    # Album matches but the artist doesn't -> reject (don't embed wrong art).
    assert art_lookup._deezer_art("Adele", "21", {}) is None


def test_itunes_art_returns_first_matching_album_image(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_albums.return_value = [
        SimpleNamespace(name="B", artists=["A"], image_url=None),          # match but no art -> skip
        SimpleNamespace(name="Wrong", artists=["A"], image_url="http://it/wrong.jpg"),  # art but wrong album -> skip
        SimpleNamespace(name="B (Deluxe)", artists=["A"], image_url="http://it/600.jpg"),  # match + art
    ]
    monkeypatch.setattr(registry, "get_itunes_client", lambda *a, **k: client)
    assert art_lookup._itunes_art("A", "B", {}) == "http://it/600.jpg"


def test_audiodb_art_extracts_thumb(monkeypatch):
    import core.audiodb_client as adb
    fake = MagicMock()
    fake.search_album.return_value = {"strAlbum": "B", "strArtist": "A",
                                      "strAlbumThumb": "http://adb/cover.jpg"}
    monkeypatch.setattr(adb, "AudioDBClient", lambda *a, **k: fake)
    assert art_lookup._audiodb_art("A", "B", {}) == "http://adb/cover.jpg"


def test_spotify_art_uses_connected_client(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_albums.return_value = [
        SimpleNamespace(name="B", artists=["A"], image_url="http://sp/640.jpg")]
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: client)
    assert art_lookup._spotify_art("A", "B", {}) == "http://sp/640.jpg"


# --- album-match validation (the wrong-art guard) ---


def test_album_matches_exact_and_suffix_variants():
    assert art_lookup._album_matches("Taylor Swift", "1989", "Taylor Swift", "1989")
    assert art_lookup._album_matches("Taylor Swift", "1989", "Taylor Swift", "1989 (Deluxe)")
    assert art_lookup._album_matches("Pink Floyd", "The Dark Side of the Moon",
                                     "Pink Floyd", "Dark Side of the Moon - Remastered")


def test_album_matches_multi_artist_and_feat():
    assert art_lookup._album_matches("Drake", "Scorpion", "Drake & Future", "Scorpion")
    assert art_lookup._album_matches("Tyler, The Creator", "IGOR", "Tyler The Creator", "IGOR")


def test_album_matches_rejects_wrong_album_or_artist():
    assert not art_lookup._album_matches("Adele", "21", "Adele", "Completely Different")
    # Generic album title, different artist -> the artist gate rejects it.
    assert not art_lookup._album_matches("Coldplay", "Greatest Hits", "Other Band", "Greatest Hits")
    assert not art_lookup._album_matches("Adele", "21", "Adele", "")
    assert not art_lookup._album_matches("Adele", "", "Adele", "21")


def test_album_matches_unknown_requested_artist_allows_album_match():
    # cover.jpg path may lack artist context -> album match alone suffices.
    assert art_lookup._album_matches("", "1989", "Taylor Swift", "1989")


# ---------------------------------------------------------------------------
# build_art_lookup — caching + guarding
# ---------------------------------------------------------------------------


def test_lookup_is_cached_per_source(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = {"title": "B", "artist": {"name": "A"},
                                        "cover_xl": "http://x/xl.jpg"}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    lookup = art_lookup.build_art_lookup("A", "B", {})
    first = lookup("deezer")
    second = lookup("deezer")
    assert first == second == "http://x/xl.jpg"
    # Cached: the underlying client was only hit once across both calls.
    assert client.search_album.call_count == 1


def test_lookup_guards_source_exceptions(monkeypatch):
    import core.metadata.registry as registry
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(registry, "get_deezer_client", _boom)
    lookup = art_lookup.build_art_lookup("A", "B", {})
    assert lookup("deezer") is None  # swallowed, not raised


def test_lookup_unknown_source_returns_none():
    lookup = art_lookup.build_art_lookup("A", "B", {})
    assert lookup("tidal") is None
    assert lookup("bogus") is None


# ---------------------------------------------------------------------------
# select_preferred_art_url — the gate the artwork pipeline calls
# ---------------------------------------------------------------------------


def test_selector_feature_off_returns_none():
    # The critical non-breaking case: no configured order -> no-op, caller
    # keeps today's art.
    assert art_lookup.select_preferred_art_url("A", "B", {}, None) is None
    assert art_lookup.select_preferred_art_url("A", "B", {}, []) is None
    assert art_lookup.select_preferred_art_url("A", "B", {}, "deezer") is None


def test_selector_resolves_first_available_source(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = {"title": "B", "artist": {"name": "A"},
                                        "cover_xl": "http://x/xl.jpg"}
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    # Order lists an unsupported source first (filtered out), then deezer.
    url = art_lookup.select_preferred_art_url("A", "B", {}, ["tidal", "deezer"])
    assert url == "http://x/xl.jpg"


def test_selector_none_when_order_has_no_available_sources(monkeypatch):
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, "get_client_for_source", lambda s: None)  # spotify off
    # 'tidal' unsupported, 'spotify' unavailable -> empty effective order.
    assert art_lookup.select_preferred_art_url("A", "B", {}, ["tidal", "spotify"]) is None


def test_selector_none_when_nothing_resolves(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_album.return_value = None  # deezer has no match
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: client)
    assert art_lookup.select_preferred_art_url("A", "B", {}, ["deezer"]) is None


def test_selector_caa_uses_release_mbid(monkeypatch):
    url = art_lookup.select_preferred_art_url(
        "A", "B", {"musicbrainz_release_id": "mbid-9"}, ["caa"])
    assert url == "https://coverartarchive.org/release/mbid-9/front-1200"
