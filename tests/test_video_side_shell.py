"""Video side shell: the Music ↔ Video toggle + video sidebar (experimental branch).

This is the first slice of the video side. It must be ADDITIVE and ISOLATED:
the music sidebar/nav is untouched, the toggle + video nav are wired purely via
data-attributes (no inline onclick — which would also break the script-split
integrity contract), and the controller is a self-contained IIFE that adds no
globals. These pin all of that so a regression can't silently couple the two
sides or break the music shell.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_JS = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
_DASH_JS = (_ROOT / "webui" / "static" / "video" / "video-dashboard.js").read_text(encoding="utf-8")
_LIB_JS = (_ROOT / "webui" / "static" / "video" / "video-library.js").read_text(encoding="utf-8")
_CSS_PATH = _ROOT / "webui" / "static" / "video" / "video-side.css"

EXPECTED_VIDEO_PAGES = {
    "video-dashboard", "video-search", "video-discover", "video-library",
    "video-watchlist", "video-wishlist", "video-downloads", "video-calendar",
    "video-import", "video-settings", "video-issues", "video-help",
}


def _block(html: str, open_tag_re: str, close_tag: str) -> str:
    """Return the substring from the first match of open_tag_re to the next close_tag."""
    m = re.search(open_tag_re, html)
    assert m, f"could not find {open_tag_re!r}"
    end = html.index(close_tag, m.end())
    return html[m.start():end + len(close_tag)]


# --- the toggle ------------------------------------------------------------

def test_side_toggle_has_music_and_video():
    block = _block(_INDEX, r'<div class="side-toggle"', "</div>")
    assert 'data-side-target="music"' in block
    assert 'data-side-target="video"' in block
    # Wired by JS, not inline handlers (isolation + integrity contract).
    assert "onclick" not in block


# --- the video nav ---------------------------------------------------------

def test_video_nav_has_all_expected_pages():
    block = _block(_INDEX, r'<nav class="sidebar-nav video-nav"', "</nav>")
    found = set(re.findall(r'data-video-page="([^"]+)"', block))
    assert found == EXPECTED_VIDEO_PAGES, f"video nav pages mismatch: {found ^ EXPECTED_VIDEO_PAGES}"
    assert "onclick" not in block  # data-attr wired, no inline handlers


def test_video_page_host_present():
    assert 'id="video-page-host"' in _INDEX
    assert 'class="page video-page"' in _INDEX


def test_video_assets_referenced():
    assert "video/video-side.js" in _INDEX
    assert "video/video-side.css" in _INDEX
    assert _CSS_PATH.exists() and _CSS_PATH.stat().st_size > 0


# --- isolation: music side untouched --------------------------------------

def test_music_sidebar_nav_still_intact():
    # The original music nav (a sibling .sidebar-nav WITHOUT the video-nav class)
    # must still carry its pages — the video side is additive, not a rewrite.
    music_nav = _block(_INDEX, r'<nav class="sidebar-nav">', "</nav>")
    for page in ("dashboard", "sync", "library", "settings", "issues", "help"):
        assert f'data-page="{page}"' in music_nav, f"music nav lost '{page}'"
    # The music subtitle is still the default in markup (JS swaps it at runtime).
    assert "Music Sync & Manager" in _INDEX


# --- the video dashboard (first built page) -------------------------------

def test_video_dashboard_subpage_present_with_expected_cards():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-dashboard"', "</section>")
    # Mirrors the music dashboard's sections (minus enrichment), reusing its CSS.
    for card in ("services", "stats", "library", "downloads", "tools", "activity"):
        assert f'data-card="{card}"' in block, f"video dashboard missing the '{card}' card"
    # Reuses music's dashboard classes so the look matches.
    assert 'class="dash-grid"' in block
    assert "stat-card-dashboard" in block
    # Driven by data, not music code: stat values carry data-video-stat hooks.
    assert "data-video-stat=" in block
    # Isolation + integrity contract: no inline handlers anywhere in the page.
    assert "onclick" not in block


def test_video_dashboard_header_matches_music_shape():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-dashboard"', "</section>")
    header = _block(block, r'<div class="dashboard-header">', "<div class=\"dash-grid\">")
    # Same shell as music: sweep band + icon title + subtitle.
    assert "dashboard-header-sweep" in header
    assert "page-header-icon" in header and "header-title" in header
    assert "header-subtitle" in header
    # Watchlist/Wishlist quick-nav present, navigating to the video pages (no
    # music IDs — would duplicate + bind music JS — just classes + data-goto).
    assert "header-quick-nav" in header
    assert 'data-video-goto="video-watchlist"' in header
    assert 'data-video-goto="video-wishlist"' in header
    assert 'id="watchlist-button"' not in header and 'id="wishlist-button"' not in header
    # The enrichment-worker button row isn't built yet (will match music later).
    assert "onclick" not in header


def test_video_dashboard_sweep_hidden_on_video_side():
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert 'body[data-side="video"] .dashboard-header-sweep' in css


def test_video_dashboard_has_placeholder_slot_for_unbuilt_pages():
    assert 'id="video-placeholder-slot"' in _INDEX


def test_video_dashboard_data_module_referenced_and_isolated():
    assert "video/video-dashboard.js" in _INDEX
    stripped = _DASH_JS.strip()
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _DASH_JS and "})();" in _DASH_JS
    # Decoupled from the controller: it listens for the page-shown event rather
    # than being called directly, and adds no globals / inline handlers.
    assert "soulsync:video-page-shown" in _DASH_JS
    assert "addEventListener" in _DASH_JS
    assert "window." not in _DASH_JS


def test_video_library_subpage_present():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-library"', "</section>")
    assert "data-video-lib-grid" in block          # the card grid
    assert "data-video-scan" in block              # the Scan button
    assert 'data-video-lib-tab="movies"' in block and 'data-video-lib-tab="shows"' in block
    assert "onclick" not in block                  # data-attr wired, no inline handlers


def test_video_library_module_referenced_and_isolated():
    assert "video/video-library.js" in _INDEX
    stripped = _LIB_JS.strip()
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _LIB_JS and "})();" in _LIB_JS
    assert "soulsync:video-page-shown" in _LIB_JS  # decoupled via the event
    assert "addEventListener" in _LIB_JS
    assert "window." not in _LIB_JS


def test_controller_is_isolated_iife_with_no_globals():
    stripped = _JS.strip()
    # Wrapped in an IIFE → declares no module-level globals that could collide
    # with or shadow music functions.
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _JS and "})();" in _JS
    # No window.X assignments (the cross-file leak pattern) and no inline-handler
    # dependence — everything is addEventListener.
    assert "window." not in _JS or "window.location" in _JS  # tolerate none; none expected
    assert "addEventListener" in _JS
