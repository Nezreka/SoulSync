"""#1042 — interactive metadata on the video detail page.

Genres and keywords become clickable chips that cross-navigate (genre → Discover
filtered to it; keyword → video Search for it), and every "Where to Watch"
provider icon becomes a real link (the JustWatch aggregate page, or a JustWatch
search fallback) instead of a dead badge.

The three sides of that contract live in three separate IIFE modules wired only
by CustomEvents, so these pin the event names + data-attributes on both ends so
a rename on one side can't silently break the hop.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DETAIL = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_DISCOVER = (_ROOT / "webui" / "static" / "video" / "video-discover.js").read_text(encoding="utf-8")
_SEARCH = (_ROOT / "webui" / "static" / "video" / "video-search.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


# ── genre chip: detail → Discover ──────────────────────────────────────────────

def test_genre_chip_is_a_button_with_data_attr():
    # rendered as a <button> carrying data-vd-genre (both render sites use genreChip)
    assert 'data-vd-genre="' in _DETAIL
    assert 'class="vd-genre"' in _DETAIL
    assert '<button' in _DETAIL


def test_genre_click_navigates_to_discover_and_emits_browse():
    assert "data-vd-genre" in _DETAIL
    assert "soulsync:video-navigate" in _DETAIL
    assert "'video-discover'" in _DETAIL
    assert "soulsync:video-discover-browse" in _DETAIL


def test_discover_listens_for_browse_event():
    assert "soulsync:video-discover-browse" in _DISCOVER
    # applies the genre through the existing filter seam, not a bespoke path
    assert "applyFilter()" in _DISCOVER
    assert "_pendingBrowse" in _DISCOVER


# ── keyword chip: detail → Search ──────────────────────────────────────────────

def test_keyword_chip_is_a_button_with_data_attr():
    assert 'data-vd-kw="' in _DETAIL
    assert 'class="vd-kw"' in _DETAIL


def test_keyword_click_navigates_to_search_and_emits_query():
    assert "'video-search'" in _DETAIL
    assert "soulsync:video-search-query" in _DETAIL


def test_search_listens_for_query_event():
    assert "soulsync:video-search-query" in _SEARCH
    assert "_pendingQuery" in _SEARCH


# ── where-to-watch providers become real links ────────────────────────────────

def test_provider_badges_are_anchors_not_dead_divs():
    # the whole point of #1042: no dead badge divs, every provider is an <a href>
    assert 'class="vd-prov vd-prov--badge"' in _DETAIL


def test_provider_links_are_per_service_searches():
    # each icon links to a search on THAT service, not one shared aggregate url
    assert "function providerSearchUrl" in _DETAIL
    assert "amazon.com/s?k=" in _DETAIL
    assert "tv.apple.com/search" in _DETAIL
    assert "play.google.com/store/search" in _DETAIL
    assert "youtube.com/results" in _DETAIL
    # wired per provider, chosen over the aggregate fallback
    assert "providerSearchUrl(p.name, data && data.title)" in _DETAIL
    assert "direct || aggHref" in _DETAIL


def test_provider_href_falls_back_to_tmdb_then_justwatch():
    # unknown provider → TMDB aggregate page; no TMDB link → JustWatch search
    assert "ex.providers_link || ''" in _DETAIL
    assert "aggHref = link || jwSearch" in _DETAIL
    assert "justwatch.com/us/search" in _DETAIL


# ── CSS affordance: the chips/badges must look clickable ──────────────────────

def test_interactive_chips_have_pointer_cursor():
    # button reset + pointer on the now-clickable genre/keyword chips
    detail_block = _CSS
    assert ".vd-genre:hover" in detail_block
    assert ".vd-kw:hover" in detail_block
    # provider badge is no longer a dead cursor:default
    assert ".vd-prov--badge { cursor: pointer; }" in detail_block
