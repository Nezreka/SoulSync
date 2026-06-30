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


def test_itunes_art_upgrades_to_max_resolution(monkeypatch):
    import core.metadata.registry as registry
    client = MagicMock()
    client.search_albums.return_value = [
        SimpleNamespace(name="GNX", artists=["Kendrick Lamar"],
                        image_url="https://is1.mzstatic.com/image/source/600x600bb.jpg")]
    monkeypatch.setattr(registry, "get_itunes_client", lambda *a, **k: client)
    # The 600x600 default is bumped to the max so iTunes contributes big art.
    assert art_lookup._itunes_art("Kendrick Lamar", "GNX", {}) == \
        "https://is1.mzstatic.com/image/source/3000x3000bb.jpg"


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


def test_album_matches_rejects_numeric_difference():
    """Sokhi: same series, different volume number. CJK strips to latin
    tokens, so Vol.4 was a token-subset of Vol.4.5 and inherited its art.
    A number on only one side = a different release, never a suffix."""
    A = "B小町"
    assert not art_lookup._album_matches(
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4",
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4.5")
    assert not art_lookup._album_matches(
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.2",
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.2.5")
    # Sequels are different albums too.
    assert not art_lookup._album_matches("Artist", "Album", "Artist", "Album 2")
    # Identical volume numbers still match.
    assert art_lookup._album_matches(
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4",
        A, "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4")
    # Numeric token shared by BOTH sides keeps non-numeric suffix tolerance.
    assert art_lookup._album_matches("Taylor Swift", "1989", "Taylor Swift", "1989 (Deluxe)")


def test_album_matches_rejects_cjk_trailing_sequel_digit():
    """Sokhi #2: the sequel '2' is glued straight onto a CJK word
    ('…サウンドトラック2'), and '第2期' (season 2) already puts a '2' on both
    sides — so the digit-strip collapsed both to {'2'} and the cour-2
    soundtrack's cover hung on the base soundtrack."""
    ART = "藤澤慶昌"
    OST = "『無職転生 〜異世界行ったら本気だす〜』 第2期 オリジナル・サウンドトラック"
    assert not art_lookup._album_matches(ART, OST, ART, OST + "2")
    assert not art_lookup._album_matches(ART, OST + "2", ART, OST)
    # The genuine base-album hit still matches (incl. its shared 第2期).
    assert art_lookup._album_matches(ART, OST, ART, OST)


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


# ---------------------------------------------------------------------------
# Cover-art PICKER — candidate gathering (CAA multi-image + single sources)
# ---------------------------------------------------------------------------


def test_parse_caa_images_prefers_1200_and_fronts_first():
    data = {"images": [
        {"image": "http://caa/back.jpg", "thumbnails": {"1200": "http://caa/back-1200.jpg"},
         "types": ["Back"], "front": False},
        {"image": "http://caa/front.jpg",
         "thumbnails": {"1200": "http://caa/front-1200.jpg", "500": "http://caa/front-500.jpg"},
         "types": ["Front"], "front": True},
    ]}
    out = art_lookup._parse_caa_images(data)
    assert [c["url"] for c in out] == ["http://caa/front-1200.jpg", "http://caa/back-1200.jpg"]
    assert out[0]["source"] == "caa" and out[0]["front"] is True
    assert out[0]["type"] == "Front" and out[1]["type"] == "Back"


def test_parse_caa_images_falls_back_to_full_image_and_skips_urlless():
    data = {"images": [
        {"image": "http://caa/only-full.jpg", "front": True},   # no thumbnails -> use image
        {"types": ["Front"]},                                   # no url at all -> skipped
        "not-a-dict",                                           # ignored
    ]}
    assert [c["url"] for c in art_lookup._parse_caa_images(data)] == ["http://caa/only-full.jpg"]


def test_parse_caa_images_empty():
    assert art_lookup._parse_caa_images(None) == []
    assert art_lookup._parse_caa_images({"images": []}) == []


def test_gather_combines_caa_and_single_sources_deduped():
    caa = [{"url": "http://caa/front-1200.jpg", "source": "caa", "type": "Front", "front": True}]
    singles = {"deezer": "http://dz/cover.jpg", "itunes": "http://it/cover.jpg",
               "spotify": None, "audiodb": "http://caa/front-1200.jpg"}  # audiodb dups the CAA url
    out = art_lookup.gather_album_art_candidates(
        "Kendrick Lamar", "DAMN.", {}, lookup=lambda s: singles.get(s), caa_candidates=caa)
    # CAA first, then deezer + itunes; spotify(None) skipped; audiodb dropped (dup url)
    assert [c["url"] for c in out] == [
        "http://caa/front-1200.jpg", "http://dz/cover.jpg", "http://it/cover.jpg"]
    assert out[0]["source"] == "caa"


def test_gather_skips_unavailable_sources(monkeypatch):
    monkeypatch.setattr(art_lookup, "is_art_source_available", lambda s: s != "spotify")
    out = art_lookup.gather_album_art_candidates(
        "A", "B", {}, lookup=lambda s: f"http://{s}.jpg", caa_candidates=[])
    assert {c["source"] for c in out} == {"deezer", "itunes", "audiodb"}


def test_gather_guards_a_failing_source(monkeypatch):
    monkeypatch.setattr(art_lookup, "is_art_source_available", lambda s: True)

    def _lookup(s):
        if s == "itunes":
            raise RuntimeError("boom")
        return f"http://{s}.jpg"

    out = art_lookup.gather_album_art_candidates("A", "B", {}, lookup=_lookup, caa_candidates=[])
    sources = {c["source"] for c in out}
    assert "itunes" not in sources and "deezer" in sources
