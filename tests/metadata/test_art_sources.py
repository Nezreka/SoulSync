"""Unit tests for the pure cover-art source selection logic.

Pins the ordering + fallback + back-compat contract that the artwork
integration relies on. No network, config, or DB — the per-source lookups are
injected, so these tests are fast and deterministic.
"""

from __future__ import annotations

from core.metadata.art_sources import (
    ART_CAPABLE_SOURCES,
    effective_art_order,
    resolve_cover_art,
)


# ---------------------------------------------------------------------------
# effective_art_order — config resolution + legacy back-compat
# ---------------------------------------------------------------------------


def test_configured_order_wins_and_is_normalized():
    assert effective_art_order(["Deezer", " CAA ", "iTunes"]) == ["deezer", "caa", "itunes"]


def test_unknown_sources_filtered_out():
    # 'genius'/'lastfm' aren't art-capable; 'bogus' is unknown.
    assert effective_art_order(["genius", "deezer", "bogus", "lastfm"]) == ["deezer"]


def test_duplicates_collapsed_keeping_first_position():
    assert effective_art_order(["deezer", "caa", "deezer", "CAA"]) == ["deezer", "caa"]


def test_empty_order_with_prefer_caa_is_legacy_caa_first():
    # Back-compat: an un-migrated install with prefer_caa_art on behaves as
    # 'CAA first, then the download's own art' — exactly today's logic.
    assert effective_art_order([], prefer_caa_art=True) == ["caa"]
    assert effective_art_order(None, prefer_caa_art=True) == ["caa"]


def test_empty_order_without_prefer_caa_is_default_only():
    # The critical non-breaking case: no list + no prefer_caa => empty order,
    # so the caller uses the download's own art (today's default).
    assert effective_art_order([], prefer_caa_art=False) == []
    assert effective_art_order(None) == []
    assert effective_art_order("not-a-list") == []


def test_all_invalid_entries_fall_back_to_legacy():
    assert effective_art_order(["genius", "lastfm"], prefer_caa_art=True) == ["caa"]
    assert effective_art_order(["genius", "lastfm"], prefer_caa_art=False) == []


def test_art_capable_sources_excludes_lyrics_only_sources():
    assert "caa" in ART_CAPABLE_SOURCES
    assert "deezer" in ART_CAPABLE_SOURCES
    assert "genius" not in ART_CAPABLE_SOURCES
    assert "lastfm" not in ART_CAPABLE_SOURCES


# ---------------------------------------------------------------------------
# resolve_cover_art — ordered walk + fallback + robustness
# ---------------------------------------------------------------------------


def test_first_source_with_art_wins():
    art = {"caa": "http://caa/x.jpg", "deezer": "http://dz/y.jpg"}
    url, src = resolve_cover_art(["deezer", "caa"], art.get)
    assert (url, src) == ("http://dz/y.jpg", "deezer")


def test_falls_through_to_next_source_when_missing():
    art = {"deezer": None, "caa": "http://caa/x.jpg"}
    url, src = resolve_cover_art(["deezer", "caa"], art.get)
    assert (url, src) == ("http://caa/x.jpg", "caa")


def test_returns_none_when_nothing_resolves():
    url, src = resolve_cover_art(["deezer", "caa"], lambda _s: None)
    assert (url, src) == (None, None)


def test_empty_order_returns_none_so_caller_uses_default():
    url, src = resolve_cover_art([], lambda _s: "http://should/not/be/called.jpg")
    assert (url, src) == (None, None)


def test_validate_rejection_skips_to_next_source():
    art = {"deezer": "http://dz/tiny.jpg", "caa": "http://caa/big.jpg"}
    # Pretend deezer's image fails validation (e.g. too small / placeholder).
    def _validate(source, url):
        return source != "deezer"
    url, src = resolve_cover_art(["deezer", "caa"], art.get, validate=_validate)
    assert (url, src) == ("http://caa/big.jpg", "caa")


def test_lookup_exception_is_treated_as_miss_not_fatal():
    def _lookup(source):
        if source == "deezer":
            raise RuntimeError("network down")
        return "http://caa/x.jpg"
    url, src = resolve_cover_art(["deezer", "caa"], _lookup)
    assert (url, src) == ("http://caa/x.jpg", "caa")


def test_validate_exception_is_treated_as_miss():
    def _validate(source, url):
        if source == "deezer":
            raise ValueError("bad image header")
        return True
    art = {"deezer": "http://dz/x.jpg", "caa": "http://caa/x.jpg"}
    url, src = resolve_cover_art(["deezer", "caa"], art.get, validate=_validate)
    assert (url, src) == ("http://caa/x.jpg", "caa")
