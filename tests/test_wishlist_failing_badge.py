"""Failing-wishlist visibility (LiveLeak's hub, phase 1).

The wishlist API has always returned retry_count / last_attempted /
failure_reason per track — the page just never rendered them. Phase 1 pins
the surface: the nebula parses the retry data, marks tracks at the failing
threshold, rolls the count up to the artist orb, and the bar gets a
Failing-only filter chip. Attribute values built from wishlist data must go
through the attr-safe escape (escapeHtml leaves double quotes intact).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "api-monitor.js").read_text(encoding="utf-8", errors="replace")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8", errors="replace")


def test_parser_carries_the_retry_data():
    assert "Number(track.retry_count)" in _JS
    assert "track.last_attempted" in _JS
    assert "track.failure_reason" in _JS
    assert "WL_FAILING_ATTEMPTS" in _JS


def test_failing_marks_reach_all_three_surfaces():
    # album tile track rows, singles moons, and the orb meta rollup
    assert "wl-failing-badge" in _JS
    assert "wl-moon-failing" in _JS
    assert "wl-orb-meta-failing" in _JS
    assert 'data-failing="${failingCount}"' in _JS


def test_failing_filter_chip_is_wired():
    assert 'id="wl-failing-filter"' in _HTML
    assert "_toggleFailingFilter()" in _HTML
    assert "function _toggleFailingFilter" in _JS
    # the filter respects the rollup attribute
    assert "g.dataset.failing" in _JS


def test_titles_use_the_attr_safe_escape():
    # escapeHtml (innerHTML-based) leaves double quotes intact — a failure
    # reason containing one would break out of the title attribute
    assert "function _wlAttr" in _JS
    assert '_wlAttr(_wlFailTitle(tr))' in _JS
    assert '_wlAttr(_wlFailTitle(s))' in _JS
    assert 'title="${escapeHtml(_wlFailTitle' not in _JS


def test_css_covers_every_new_class():
    for cls in (".wl-failing-badge", ".wl-moon-failing", ".wl-orb-meta-failing",
                ".wl-failing-filter.active", ".wl-track-failing"):
        assert cls in _CSS, f"missing CSS for {cls}"
