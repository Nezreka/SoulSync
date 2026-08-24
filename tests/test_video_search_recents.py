"""Search page: recent-search chips + keyboard fast paths (contract tests).

Recents are remembered on COMMIT (opening a result), never on raw keystrokes,
so the list holds real queries instead of every typo prefix. Chips render on
the idle page above Trending; Enter opens the top result; Escape clears back
to idle.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-search.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_recents_remembered_on_commit_not_keystroke():
    open_fn = _JS.split("function openCard")[1].split("function wire")[0]
    assert "rememberSearch(lastQuery)" in open_fn
    # the debounced search path must NOT remember (typo prefixes)
    run_fn = _JS.split("function runSearch")[1].split("function runChannel")[0]
    assert "rememberSearch" not in run_fn


def test_recents_deduped_capped_and_renderable():
    fn = _JS.split("function rememberSearch")[1].split("function recentsHTML")[0]
    assert "toLowerCase()" in fn          # case-insensitive dedupe
    assert "slice(0, 8)" in fn            # capped
    assert "data-vsr-recent=" in _JS and "data-vsr-recent-clear" in _JS
    # idle page shows them even before trending has loaded
    idle = _JS.split("function showIdle")[1].split("function _json")[0]
    assert "recentsHTML()" in idle


def test_chip_click_and_clear_are_wired():
    wire_fn = _JS.split("function wire")[1]
    assert "closest('[data-vsr-recent]')" in wire_fn
    assert "closest('[data-vsr-recent-clear]')" in wire_fn
    assert "localStorage.removeItem('vsRecent')" in wire_fn


def test_keyboard_enter_opens_top_result_escape_clears():
    wire_fn = _JS.split("function wire")[1]
    assert "e.key !== 'Enter'" in wire_fn and "openCard(first)" in wire_fn
    assert "e.key === 'Escape'" in wire_fn


def test_css_exists_for_chips():
    assert ".vsr-recent-chip" in _CSS and ".vsr-recent-clear" in _CSS

def test_basic_search_runs_in_app_provider_searches():
    assert "function setMode" in _JS and "function renderBasicPreview" in _JS
    assert "[data-vsr-basic-form]" in _JS
    assert "data-vsr-basic-focus" in _JS
    assert "/api/video/downloads/search/start" in _JS and "/api/video/downloads/search/poll" in _JS
    assert "/api/video/downloads/config" in _JS and "basicIdsFromDownloadConfig" in _JS
    assert "source: 'soulseek'" in _JS and "indexer: 'thepiratebay'" in _JS and "source: 'usenet'" in _JS
    assert "kind: 'FlareSolverr torrent scraper', source: 'extto'" in _JS
    assert "thepiratebay.org" not in _JS and "1337x.to" not in _JS and 'target="_blank"' not in _JS
    basic_fn = _JS.split("function renderBasicPreview")[1].split("function freshPeriodLabel")[0]
    assert "fetch(" not in basic_fn
    assert ".vsr-tabs" in _CSS and ".vsr-basic" in _CSS and ".vsr-basic-hit" in _CSS
    assert "data-vsr-basic-source-tab" in _JS and ".vsr-basic-source-tabs" in _CSS
    assert "vsr-basic-hit-analysis" in _JS and "vsr-basic-hit-reason" not in _JS
    assert "basicSizeLabel" in _JS and "basicHealthLabel" in _JS
    assert "vsr-basic-hit-side" in _JS and "vsr-basic-hit-linkstate" in _JS
    assert "vsr-basic-loader" in _JS and "vsr-basic-tab-loader" in _JS
    assert ".vsr-basic-hit-side" in _CSS and "vsrBasicScan" in _CSS and "vsrBasicDots" in _CSS


def test_fresh_releases_tab_fetches_extto_homepage_board_in_app():
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'data-vsr-tab="fresh"' in index and 'data-vsr-panel="fresh"' in index
    assert "Sourced from EXT.to" in index
    assert "FRESH_URL = '/api/video/downloads/fresh-releases'" in _JS
    assert "function renderFreshReleases" in _JS and "function loadFreshReleases" in _JS
    assert "data-vsr-fresh-period" in _JS and "freshSectionHTML('movies', 'Movies')" in _JS
    assert "freshSectionHTML('tv', 'TV Series')" in _JS
    fresh_fn = _JS.split("function freshRowHTML")[1].split("function freshSectionHTML")[0]
    assert "<a " not in fresh_fn and "href=" not in fresh_fn
    assert ".vsr-fresh-results" in _CSS and ".vsr-fresh-row" in _CSS and ".vsr-fresh-loader" in _CSS
    assert "repeat(3, minmax" in _CSS
