"""Video Download modal — per-source 'Auto' (search + auto-grab the best) wiring.

The download view already has a Manual search (you pick a release). This adds an
Auto button per source that runs the SAME search and then grabs the best release
for the quality profile. The "best" comes for free: the backend returns hits
sorted accepted→score→availability (see test_video_api.py
::test_downloads_search_endpoint_ranks_and_filters asserting results[0].accepted),
so Auto just takes the first accepted hit that has an uploader.

String-contract level (like tests/test_video_automations_builder.py) so a refactor
can't silently unwire the auto path or make manual + auto diverge.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VIEW = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_each_source_has_manual_and_auto_buttons():
    assert 'data-vdl-search="' in _VIEW          # Manual (pick yourself)
    assert 'data-vdl-auto="' in _VIEW            # Auto (best pick)
    # Renamed for clarity: Manual vs Auto.
    assert '⌕ Manual' in _VIEW and '⚡ Auto' in _VIEW


def test_search_all_gains_an_auto_all():
    assert 'data-vdl-auto-all' in _VIEW
    assert 'Auto all' in _VIEW


def test_click_handler_routes_auto_before_manual():
    # Auto + auto-all must be handled (and auto checked before the plain search
    # selector, since the markup nests differently).
    assert "closest('[data-vdl-auto]')" in _VIEW
    assert "closest('[data-vdl-auto-all]')" in _VIEW


def test_search_threads_a_done_callback():
    # Auto needs to act when the search SETTLES, not on the first tick.
    assert 'function searchInto(container, resultsEl, params, triggerRows, onDone)' in _VIEW
    assert 'function _pollSearch(resultsEl, params, id, triggerRows, pollMs, onDone)' in _VIEW
    assert 'if (onDone) onDone();' in _VIEW


def test_autopick_takes_first_accepted_with_uploader():
    assert 'function _autoPick(' in _VIEW
    # picks the first accepted hit that has an uploader (best, since pre-sorted)
    assert 'rows[i].accepted && rows[i].username' in _VIEW


def test_manual_and_auto_share_one_grab_path():
    # Both go through buildGrabPayload + sendGrab so they can't diverge.
    assert 'function buildGrabPayload(' in _VIEW
    assert 'function sendGrab(' in _VIEW
    assert _VIEW.count('sendGrab(buildGrabPayload(') >= 2  # doGrab + _autoPick


def test_auto_button_is_styled():
    assert '.vdl-src-auto' in _CSS
    assert '.vdl-res--auto' in _CSS          # the chosen card gets a ring
    assert '.vdl-src-actions' in _CSS
