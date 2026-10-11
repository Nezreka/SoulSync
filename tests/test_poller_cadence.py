"""Steady-state poller cadence (request-flood P3).

Two provably-noisy pollers tamed:
  • video service-status polled every 5s forever for values that change only
    when the user edits settings — now 60s, skipped while the tab is hidden,
    with INSTANT repaint on side-flip and tab refocus
  • the show-detail downloads tracker polled flat 2.5s for as long as any
    show page stayed open — now adaptive (fast only while a download for the
    show is in flight, 10s idle) and silent while hidden

The downloads PAGE poller was already adaptive (2s active / 6s idle,
self-stopping off-page) and stays untouched.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_service_status_polls_slow_and_respects_hidden():
    js = (_ROOT / "webui" / "static" / "video" / "video-service-status.js").read_text(
        encoding="utf-8", errors="replace")
    assert "60000" in js and ", 5000)" not in js
    assert "document.hidden" in js
    # instant repaint hooks: side-flip observer + tab refocus
    assert "MutationObserver" in js
    assert "visibilitychange" in js


def test_detail_download_tracker_is_adaptive():
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
        encoding="utf-8", errors="replace")
    assert "_DL_FAST_MS = 2500" in js and "_DL_IDLE_MS = 10000" in js
    assert "_dlHasActive ? _DL_FAST_MS : _DL_IDLE_MS" in js
    assert "_dlHasActive = Object.keys(cur).length > 0" in js
    # no flat interval remains for this tracker
    assert "setInterval(pollDl" not in js
    # hidden tab stays silent but the loop keeps rescheduling
    assert "if (!document.hidden) pollDl()" in js


def test_downloads_page_poller_untouched():
    # the legacy vanilla page was rewritten in React (PR #1651); the poller now
    # lives in -vdl.use-downloads.ts with the same 2s-active / 6s-idle cadence.
    vdl = _ROOT / "webui" / "src" / "routes" / "active-downloads"
    use = (vdl / "-vdl.use-downloads.ts").read_text(encoding="utf-8", errors="replace")
    types = (vdl / "-vdl.types.ts").read_text(encoding="utf-8", errors="replace")
    assert "active ? VDL_POLL_ACTIVE_MS : VDL_POLL_IDLE_MS" in use
    assert "VDL_POLL_ACTIVE_MS = 2000" in types
    assert "VDL_POLL_IDLE_MS = 6000" in types
