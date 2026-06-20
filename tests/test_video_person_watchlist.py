"""Person detail page gets the standard 'Add to Watchlist' button (the one already
on movie/show pages and person CARDS). Person follows are stored kind='person' in
video_watchlist, and the API supports add/remove/check for persons — the page just
needed the button wired.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-person.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_person_hero_has_actions_slot():
    # a dedicated slot in the person hero (managed by JS, like the movie/show pages)
    assert "data-vp-actions" in _INDEX
    assert ".vp-actions" in _CSS


def test_person_page_renders_and_toggles_watchlist():
    assert "function renderWatchlist(" in _JS
    assert "function toggleWatch(" in _JS
    # reuses the shared watchlist button chrome
    assert "library-artist-watchlist-btn" in _JS
    assert "data-vp-watch" in _JS
    # follows the person via the person-kind watchlist API
    assert "kind: 'person'" in _JS
    assert "/api/video/watchlist/add" in _JS
    assert "/api/video/watchlist/remove" in _JS
    assert "/api/video/watchlist/check" in _JS


def test_watch_button_click_is_wired():
    assert "closest('[data-vp-watch]')" in _JS
    assert "toggleWatch()" in _JS


def test_render_hydrates_the_watch_button():
    # render(d) must build the button so it shows on every person page
    i = _JS.index("function render(")
    j = _JS.index("function load(", i)
    assert "renderWatchlist(d)" in _JS[i:j]
