"""LiveLeak failing-hub follow-up: every video wishlist item gets BOTH search
buttons, Sonarr-style — auto ("Search now", the system picks) AND manual (opens
the shared release-picker modal so the USER picks). Source guards — the wishlist
UI is vanilla JS with no JS runner in this repo.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_WISHLIST_JS = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")
_DOWNLOAD_JS = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_manual_pick_rendered_on_all_three_surfaces():
    # movie card, season side, episode card each render a data-vwsh-pick button
    assert _WISHLIST_JS.count("data-vwsh-pick=") >= 1          # the pickBtn builder
    assert "pickBtn('vwsh-hunt', 'movie'" in _WISHLIST_JS
    assert "pickBtn('vwsh-szn-hunt', 'season'" in _WISHLIST_JS
    assert "pickBtn('vwsh-epc-hunt', 'episode'" in _WISHLIST_JS


def test_manual_pick_opens_the_shared_release_picker():
    assert "VideoDownload.manualSearch" in _WISHLIST_JS
    # delegation runs the pick branch before the auto-hunt branch
    assert _WISHLIST_JS.index("closest('[data-vwsh-pick]')") < _WISHLIST_JS.index("closest('[data-vwsh-hunt]')")


def test_modal_supports_movie_scope():
    # header label handles movies (was "Season undefined") and the search carries the year
    assert "scope === 'movie'" in _DOWNLOAD_JS
    assert "year: opts.year || null" in _DOWNLOAD_JS


def test_pick_buttons_do_not_overlap_the_auto_buttons():
    assert ".vwsh-movie-art .vwsh-hunt.vwsh-pick { right: 72px; }" in _CSS
    assert ".vwsh-nebula .vwsh-szn-hunt.vwsh-pick { right: 56px; }" in _CSS


def test_failing_filter_chip_wired():
    # the fix-it hub: a "⚠ Failing" chip filters the wishlist to stuck items,
    # each carrying auto + manual + remove — video parity with the music chip.
    _INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "data-vwsh-failing" in _INDEX
    assert "isFailingItem" in _WISHLIST_JS
    assert "state.failingOnly" in _WISHLIST_JS
    assert ".vwsh-failing-filter--on" in _CSS
