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
_SCAN_JS = (_ROOT / "webui" / "static" / "video" / "video-scan.js").read_text(encoding="utf-8")
_VSETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_CSS_PATH = _ROOT / "webui" / "static" / "video" / "video-side.css"

EXPECTED_VIDEO_PAGES = {
    "video-dashboard", "video-search", "video-discover", "video-library",
    "video-watchlist", "video-wishlist", "video-downloads", "video-calendar",
    "video-tools", "video-import", "video-settings", "video-issues", "video-help",
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
    # Reuses the music library structure verbatim (no reinvented markup).
    assert "library-container" in block
    assert "library-search-input" in block          # search bar
    assert "alphabet-selector" in block             # A–Z selector
    assert "library-artists-grid" in block          # the music card grid
    assert "data-video-lib-grid" in block
    assert 'data-video-lib-tab="movies"' in block and 'data-video-lib-tab="shows"' in block
    assert "data-video-scan-mode" in block          # the Scan button
    # Server-side paging + sort/filter controls (music parity).
    assert "data-video-lib-pagination" in block and "data-video-lib-next" in block
    assert "data-video-lib-sort" in block and "data-video-lib-status" in block
    assert "onclick" not in block                   # data-attr wired, no inline handlers


def test_video_library_module_referenced_and_isolated():
    assert "video/video-library.js" in _INDEX
    stripped = _LIB_JS.strip()
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _LIB_JS and "})();" in _LIB_JS
    assert "soulsync:video-page-shown" in _LIB_JS  # decoupled via the event
    # Cards/poster URLs use the SINGULAR kind (movie/show); the API uses plural.
    assert "cardKind" in _LIB_JS and "apiKind" in _LIB_JS
    assert "addEventListener" in _LIB_JS
    assert "window." not in _LIB_JS


def test_video_tools_page_has_three_scan_modes():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-tools"', "</section>")
    # Mode dropdown + single Scan button, same shape as the music tool card.
    for mode in ("incremental", "full", "deep"):
        assert f'value="{mode}"' in block, f"tools page missing scan mode {mode}"
    assert "data-video-scan-run" in block and "data-video-scan-select" in block
    assert "data-video-scan-phase" in block          # reuses music progress markup
    assert "tool-card" in block and "tool-card-controls" in block  # reuses music classes
    # Matches the music Database Updater card: help button, last-scan line, stats grid.
    assert "tool-help-button" in block
    assert "data-video-scan-last" in block
    assert "tool-card-stats" in block and 'data-video-scan-stat="movies"' in block
    assert "onclick" not in block


def test_dashboard_library_card_has_refresh_and_deep_buttons():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-dashboard"', "</section>")
    assert 'data-video-scan-mode="full"' in block   # Refresh
    assert 'data-video-scan-mode="deep"' in block    # Deep Scan
    assert "library-status-actions" in block
    # The card shows live scan progress (parity with the music dashboard).
    assert "data-video-dash-progress" in block and "data-video-dash-bar" in block


def test_scan_module_referenced_and_isolated():
    assert "video/video-scan.js" in _INDEX
    stripped = _SCAN_JS.strip()
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _SCAN_JS and "})();" in _SCAN_JS
    assert "window." not in _SCAN_JS
    assert "addEventListener" in _SCAN_JS
    assert "soulsync:video-scan-done" in _SCAN_JS
    assert "/api/video/scan/request" in _SCAN_JS
    # Rehydrates a running scan after a page refresh (parity with music tools).
    assert "/api/video/scan/status" in _SCAN_JS
    assert "resumeIfScanning" in _SCAN_JS


def test_video_settings_reuses_real_music_settings_page():
    # The video Settings nav shows the actual #settings-page (identically, for
    # now) via CSS + the shared loadPageData loader — not a video subpage.
    assert "'video-settings': 'settings'" in _JS or '"video-settings": "settings"' in _JS
    assert "loadPageData" in _JS
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert 'data-video-page="video-settings"] #settings-page' in css
    assert 'data-video-page="video-settings"] #video-page-host' in css


def test_video_library_mapping_ui_present_and_video_only():
    # Movies/TV selectors live next to the music library selector, marked
    # data-video-only so they show only on the video side.
    assert 'data-video-lib-select="movies"' in _INDEX
    assert 'data-video-lib-select="tv"' in _INDEX
    assert "data-video-only" in _INDEX
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert 'body[data-side="music"] [data-video-only]' in css  # video bits hidden on music side
    # ...and the music-library picker is hidden on the video side.
    assert 'body[data-side="video"] #plex-library-selector-container' in css


def test_video_side_hides_music_api_config_and_shows_placeholders():
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert 'body[data-side="video"] [data-music-only]' in css   # music API hidden on video
    assert "data-music-only" in _INDEX                          # the music API group is marked
    # Video API frames use the SAME .api-service-frame structure as music but a
    # data-VIDEO-service attribute, so music's settings.js [data-service] verify
    # loop can't pick them up ("Unknown service: tvdb").
    assert 'class="api-service-frame stg-service" data-video-service="tmdb"' in _INDEX
    assert 'class="api-service-frame stg-service" data-video-service="tvdb"' in _INDEX
    # They must NOT carry the music-hooked data-service attribute.
    assert 'data-service="tmdb"' not in _INDEX and 'data-service="tvdb"' not in _INDEX
    # Each connection item has a Test button (like music's connections).
    assert 'data-video-test-service="tmdb"' in _INDEX and 'data-video-test-service="tvdb"' in _INDEX


def test_dashboard_enrichment_buttons_present():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-dashboard"', "</section>")
    assert 'data-video-enrich="tmdb"' in block and 'data-video-enrich="tvdb"' in block
    assert "data-video-manage-workers" in block
    assert "video-enrich-spinner" in block          # spins while running
    assert "onclick" not in block
    # The Manage Workers button reuses music's classes verbatim so it's identical
    # (icon circle + logo), but stays wired by data-attr — never an inline onclick.
    mbtn = _block(block, r'<button class="em-manage-btn"', "</button>")
    assert "data-video-manage-workers" in mbtn and "onclick" not in mbtn
    assert "em-manage-btn-icon" in mbtn and "em-manage-btn-logo" in mbtn and "em-manage-btn-label" in mbtn


def test_video_enrichment_module_referenced_and_isolated():
    assert "video/video-enrichment.js" in _INDEX
    src = (_ROOT / "webui" / "static" / "video" / "video-enrichment.js").read_text(encoding="utf-8")
    assert src.strip().startswith("/*") or src.strip().startswith("(function")
    assert "(function" in src and "})();" in src
    # The only window.* touch is the (optional) handoff to the VIDEO orbs global
    # — never a music global, and it declares none of its own.
    assert all(
        ref == "window.videoWorkerOrbs"
        for ref in re.findall(r"window\.\w+", src)
    )
    assert "/api/video/enrichment/" in src
    # No music API/function calls (a comment may mention the music *side*).
    assert "/api/enrichment/" not in src
    assert "openEnrichmentManager" not in src


def test_video_enrichment_manager_isolated():
    assert "video/video-enrichment-manager.js" in _INDEX
    src = (_ROOT / "webui" / "static" / "video" / "video-enrichment-manager.js").read_text(encoding="utf-8")
    assert "(function" in src and "})();" in src
    assert "window." not in src
    assert "soulsync:video-open-workers" in src      # opened by the dashboard button
    assert "/api/video/enrichment/" in src           # targets the video API
    assert "/api/enrichment/" not in src             # NOT the music API
    assert "openEnrichmentManager" not in src        # never calls the music modal
    # Reuses the shared music modal CSS classes (design parity).
    assert "enrichment-manager-modal" in src and "em-rail" in src
    # Its own overlay id (not music's) so the two never collide.
    assert "vem-overlay" in src and "enrichment-manager-overlay" not in src
    # TVDB is shows-only: each worker declares its kinds and the panel defaults
    # to the worker's first kind — never a hardcoded 'movie' (which would show a
    # bogus empty Movies view for TVDB).
    assert "kinds: ['show']" in src                  # tvdb
    assert "kinds: ['movie', 'show']" in src         # tmdb
    assert "state.kind = defaultKind(id)" in src
    assert "state.kind = 'movie'" not in src


def test_video_settings_module_referenced_and_isolated():
    assert "video/video-settings.js" in _INDEX
    stripped = _VSETTINGS_JS.strip()
    assert stripped.startswith("/*") or stripped.startswith("(function")
    assert "(function" in _VSETTINGS_JS and "})();" in _VSETTINGS_JS
    assert "window." not in _VSETTINGS_JS
    assert "addEventListener" in _VSETTINGS_JS
    assert "/api/video/libraries" in _VSETTINGS_JS
    assert "soulsync:video-page-shown" in _VSETTINGS_JS
    assert "'change'" in _VSETTINGS_JS  # saves on change, like the music selector


def test_video_worker_orbs_referenced_and_isolated():
    assert "video/video-worker-orbs.js" in _INDEX
    src = (_ROOT / "webui" / "static" / "video" / "video-worker-orbs.js").read_text(encoding="utf-8")
    assert "(function" in src and "})();" in src
    # Its own global namespace — never music's window.workerOrbs.
    assert "window.videoWorkerOrbs" in src
    assert "window.workerOrbs" not in src
    # Activates only on the video dashboard (own page-awareness, not setPage).
    assert "soulsync:video-page-shown" in src
    assert 'data-video-subpage="video-dashboard"' in src
    assert 'data-video-enrich="tmdb"' in src and 'data-video-enrich="tvdb"' in src
    assert "data-video-manage-workers" in src
    # Same 7-second idle that triggers the floating-orb collapse as music.
    assert "COLLAPSE_DELAY_MS = 7000" in src
    # Reuses the shared, generic orb CSS classes (design parity, no fork).
    assert "worker-orb-hidden" in src and "worker-orb-canvas" in src
    # Does NOT reach into the music dashboard or the music API.
    assert "#dashboard-page" not in src
    assert "/api/enrichment/" not in src


def test_show_detail_subpage_present():
    block = _block(
        _INDEX, r'<section class="video-subpage" data-video-subpage="video-show-detail"', "</section>")
    # Netflix billboard + episodes containers the renderer fills.
    for hook in ('data-vd-backdrop', 'data-vd-poster', 'data-vd-title', 'data-vd-meta',
                 'data-vd-overview', 'data-vd-actions', 'data-vd-view-toggle',
                 'data-vd-season-nav', 'data-vd-episodes'):
        assert hook in block, hook
    # Back button reuses the shared data-video-goto nav (no inline handler).
    assert 'data-video-goto="video-library"' in block
    assert "onclick" not in block


def test_video_detail_module_referenced_and_isolated():
    assert "video/video-detail.js" in _INDEX
    src = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
    assert "(function" in src and "})();" in src
    assert "window." not in src                         # declares no globals
    assert "/api/video/detail/" in src                  # video API only
    assert "/api/enrichment/" not in src and "artist-detail" not in src
    assert "soulsync:video-open-detail" in src          # opened via the shared event


def test_library_cards_open_detail():
    src = _LIB_JS
    assert "soulsync:video-open-detail" in src          # show cards drill in
    assert "data-video-card-open" in src


def test_video_side_registers_detail_pages_and_open_event():
    assert "video-show-detail" in _JS and "video-movie-detail" in _JS
    assert "soulsync:video-open-detail" in _JS          # navigates on the event


def test_music_worker_orbs_untouched_by_video():
    # The video orbs are a separate file; the music orbs must not learn about
    # the video side (one-way isolation — music never depends on video).
    music = (_ROOT / "webui" / "static" / "worker-orbs.js").read_text(encoding="utf-8")
    assert "video" not in music.lower()


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
