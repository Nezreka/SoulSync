"""Downloads page UX overhaul — string-contract level (like test_video_side_shell.py), so a
refactor that silently drops the per-type theming, the sidebar count, or the new statuses
fails here."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-downloads-page.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")


def test_cards_carry_a_type_for_cinema_theming():
    assert "function dlType(" in _JS
    assert "data-vtype" in _JS                       # set on each card
    # the three Cinema palette colours are defined per type
    for vt in ('[data-vtype="movie"]', '[data-vtype="tv"]', '[data-vtype="youtube"]'):
        assert vt in _CSS, vt
    assert "--vt: 79, 143, 247" in _CSS and "--vt: 240, 69, 75" in _CSS   # azure + red


def test_importing_is_a_first_class_active_status():
    assert "importing:" in _JS and "import_failed:" in _JS
    assert "s === 'importing'" in _JS                # counts as active
    assert "vdpg-prog-indet" in _JS                  # indeterminate sweep for queued/searching/importing


def test_sidebar_has_a_live_downloads_count():
    assert "data-video-downloads-badge" in _INDEX     # the nav badge element
    assert "function setDownloadsBadge(" in _JS
    assert "function badgePoll(" in _JS               # stays live off-page too
