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
# the orb itself (one for music and video) lives in the shared parts
_PARTS = (_WL / "-ui" / "nebula-parts.tsx").read_text(encoding="utf-8", errors="replace")
# the artist detail moved out of the orb into a panel beside the nebula
_SHEET = (_WL / "-ui" / "wishlist-artist-sheet.tsx").read_text(encoding="utf-8", errors="replace")
_PAGE = (_WL / "-ui" / "wishlist-page.tsx").read_text(encoding="utf-8", errors="replace")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8", errors="replace")
_PREMIER = (_ROOT / "webui" / "static" / "wishlist-premier.css").read_text(
    encoding="utf-8", errors="replace")


def test_parser_carries_the_retry_data():
    assert "row.retry_count" in _HELPERS
    assert "row.last_attempted" in _HELPERS
    assert "row.failure_reason" in _HELPERS
    assert "WL_FAILING_ATTEMPTS" in _HELPERS
    # the threshold itself stays declared once, with its reasoning
    assert "export const WL_FAILING_ATTEMPTS = 3" in _TYPES


def test_failing_marks_reach_every_surface():
    # the orb meta rollup, the panel header, and each track row in the panel
    # (album tracks and singles share one row component)
    assert "failingCount={group.failingCount}" in _ORB
    assert "stuck={group.failingCount > 0}" in _ORB
    assert "data-failing={failingCount}" in _PARTS
    assert "wl-orb-meta--stuck" in _PARTS
    assert "wlp-sheet-stuck" in _SHEET
    assert "wlp-sheet-pill${track.failing ? ' is-stuck'" in _SHEET


def test_failing_rollup_counts_albums_and_singles():
    # an artist is "failing" if anything under it is, from either surface
    assert "failingCount" in _HELPERS
    assert "singles.filter((t) => t.failing)" in _HELPERS


def test_failing_filter_chip_is_wired():
    assert "wlp-chip--warn" in _PAGE  # React rewrite renamed the old wl-failing-filter class
    assert "failing: !prev.failing" in _PAGE
    # and the filter respects the rollup
    assert "failingOnly && group.failingCount === 0" in _HELPERS


def test_css_covers_every_class():
    assert ".wl-failing-filter.active" in _CSS
    for cls in (".wl-orb-meta--stuck", ".wlp-sheet-stuck", ".wlp-sheet-pill.is-stuck",
                ".wlp-sheet-track.is-stuck", ".wlp-sheet-chip"):
        assert cls in _PREMIER, f"missing CSS for {cls}"


# ── phase 2: manual-search jump ───────────────────────────────────────────────

def test_manual_search_opens_the_file_inspector():
    # "Search manually" is for when the automatic download failed: the person
    # wants a FILE. It opens the candidate inspector on the wishlist's own
    # source search and downloads exactly the pick. The old jump to the
    # vanilla search page is gone, so there is one path, not two.
    insp = (_ROOT / "webui" / "src" / "features" / "downloads" / "inspector-modal.tsx").read_text(
        encoding="utf-8", errors="replace")
    fn = insp[insp.index("export function openWishlistInspector"):]
    fn = fn[:fn.index("\nexport function", 10)]
    assert "searchUrl: '/api/wishlist/inspect'" in fn
    assert "'/api/wishlist/inspect/download'" in fn
    api = (_ROOT / "webui" / "static" / "api-monitor.js").read_text(
        encoding="utf-8", errors="replace")
    assert "_searchWishlistTrackManually" not in api


def test_every_track_row_gets_the_search_button():
    # album tracks and singles render through one SheetTrack, so one call
    # covers both, with the row id, the track and the artist
    assert _SHEET.count("openWishlistInspector({") == 1
    assert "aria-label={`Interactive Search for ${track.track}`}" in _SHEET
    fn = _SHEET[_SHEET.index("openWishlistInspector({"):]
    fn = fn[:fn.index("})")]
    for field in ("id: track.id", "name: track.track", "artist: track.artist", "album: track.album"):
        assert field in fn
    # and both sections actually use it
    assert _SHEET.count("<SheetTrack") == 2


# ── deliberately dropped when the page moved to React ────────────────────────
#
# test_titles_use_the_attr_safe_escape and test_onclick_args_use_the_inline_js_escape
# guarded _wlAttr / escapeForInlineJs, which existed because the vanilla page
# built markup by string concatenation: a failure reason containing a double
# quote broke out of title="…", and an apostrophe (D'Angelo) broke
# onclick="fn('…')". React passes these as props and escapes them itself, so
# both bug classes are structurally gone rather than merely untested.
