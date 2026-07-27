"""Failing-wishlist visibility (LiveLeak's hub, phases 1-2).

The wishlist API has always returned retry_count / last_attempted /
failure_reason per track — the page just never rendered them. These pin the
surface so it cannot silently regress: the parser carries the retry data,
tracks at the threshold are marked, the count rolls up to the artist orb, the
bar has a Failing-only chip, and both track surfaces offer the manual-search
jump.

The wishlist page is a React route now (webui/src/routes/wishlist), so these
assert against that source rather than api-monitor.js. The behaviour is
unchanged; only its home moved. Two of the original assertions are gone on
purpose and are recorded at the bottom.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WL = _ROOT / "webui" / "src" / "routes" / "wishlist"

_HELPERS = (_WL / "-wishlist.helpers.ts").read_text(encoding="utf-8", errors="replace")
_TYPES = (_WL / "-wishlist.types.ts").read_text(encoding="utf-8", errors="replace")
_ORB = (_WL / "-ui" / "wishlist-orb.tsx").read_text(encoding="utf-8", errors="replace")
_PAGE = (_WL / "-ui" / "wishlist-page.tsx").read_text(encoding="utf-8", errors="replace")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8", errors="replace")


def test_parser_carries_the_retry_data():
    assert "row.retry_count" in _HELPERS
    assert "row.last_attempted" in _HELPERS
    assert "row.failure_reason" in _HELPERS
    assert "WL_FAILING_ATTEMPTS" in _HELPERS
    # the threshold itself stays declared once, with its reasoning
    assert "export const WL_FAILING_ATTEMPTS = 3" in _TYPES


def test_failing_marks_reach_all_three_surfaces():
    # album tile track rows, singles moons, and the orb meta rollup
    assert "wl-failing-badge" in _ORB
    assert "wl-moon-failing" in _ORB
    assert "wl-orb-meta-failing" in _ORB
    assert "data-failing={group.failingCount}" in _ORB


def test_failing_rollup_counts_albums_and_singles():
    # an artist is "failing" if anything under it is, from either surface
    assert "failingCount" in _HELPERS
    assert "singles.filter((t) => t.failing)" in _HELPERS


def test_failing_filter_chip_is_wired():
    assert "wl-failing-filter" in _PAGE
    assert "failing: !prev.failing" in _PAGE
    # and the filter respects the rollup
    assert "failingOnly && group.failingCount === 0" in _HELPERS


def test_css_covers_every_class():
    for cls in (".wl-failing-badge", ".wl-moon-failing", ".wl-orb-meta-failing",
                ".wl-failing-filter.active", ".wl-track-failing",
                ".wl-tile-track-search", ".wl-moon-search-btn"):
        assert cls in _CSS, f"missing CSS for {cls}"


# ── phase 2: manual-search jump ───────────────────────────────────────────────

def test_manual_search_jump_still_reaches_the_search_page():
    # The handoff drives the VANILLA search page's DOM (it polls for the
    # Soulseek source icon), so it stays in api-monitor.js and is invoked.
    api = (_ROOT / "webui" / "static" / "api-monitor.js").read_text(
        encoding="utf-8", errors="replace")
    assert "function _searchWishlistTrackManually" in api
    assert "navigateToPage('search')" in api
    assert "enhanced-search-input" in api


def test_both_track_surfaces_get_the_search_button():
    assert "wl-tile-track-search" in _ORB
    assert "wl-moon-search-btn" in _ORB
    # both call the vanilla handoff with the artist AND the track
    assert _ORB.count("window._searchWishlistTrackManually?.(") == 2


# ── deliberately dropped when the page moved to React ────────────────────────
#
# test_titles_use_the_attr_safe_escape and test_onclick_args_use_the_inline_js_escape
# guarded _wlAttr / escapeForInlineJs, which existed because the vanilla page
# built markup by string concatenation: a failure reason containing a double
# quote broke out of title="…", and an apostrophe (D'Angelo) broke
# onclick="fn('…')". React passes these as props and escapes them itself, so
# both bug classes are structurally gone rather than merely untested.
