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
    # Flat/brutalist: plain UPPERCASE labels, no icon spans, no decorative glyphs.
    assert '>MANUAL<' in _VIEW and '>AUTO<' in _VIEW
    assert '⚡' not in _VIEW and '✦' not in _VIEW   # lightning + sparkle redesigned out
    assert 'vdl-btn-ic' not in _VIEW             # icon spans are gone in the brutalist buttons


def test_header_has_single_auto_best_button():
    # The old "Manual all" + "Auto all" pair is replaced by ONE header "Auto" that
    # searches every source and grabs the single best release across all of them.
    assert 'data-vdl-auto-best' in _VIEW
    assert 'data-vdl-auto-all' not in _VIEW      # the per-source-grab-all footgun is gone
    assert 'data-vdl-search-all' not in _VIEW
    assert 'Manual all' not in _VIEW and 'Auto all' not in _VIEW


def test_auto_best_picks_one_winner_across_all_sources():
    assert 'function _autoBest(' in _VIEW
    assert 'function _grabBestAcross(' in _VIEW
    # it compares accepted+grabbable hits across every source by profile score…
    assert "r.accepted && r.username" in _VIEW
    assert '(r.score || 0) > (best.score || 0)' in _VIEW
    # …and grabs exactly ONE (no per-source loop of grabs)
    assert _VIEW.count('beginTracking(') >= 3   # def + doGrab + _autoPick (+ _grabBestAcross)


def test_click_handler_routes_auto_best_and_per_source():
    assert "closest('[data-vdl-auto-best]')" in _VIEW
    assert "closest('[data-vdl-auto]')" in _VIEW     # per-source auto still works


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


# --- Grab whole season (episode-level batch) ------------------------------

def test_episode_per_source_auto_is_wired():
    # the show modal now routes the per-episode Auto button (was previously dead),
    # reusing searchInto + _autoPick at episode scope
    assert "closest('[data-vdl-auto]')" in _VIEW
    assert "scope: 'episode'" in _VIEW
    assert "_autoPick(resA, rowA)" in _VIEW


def test_grab_whole_season_button_and_batch():
    assert 'data-vdl-season-grab="' in _VIEW          # per-season button
    assert '>Grab season<' in _VIEW
    assert 'function grabSeason(' in _VIEW
    assert 'function autoGrabEpisode(' in _VIEW
    # episode-LEVEL: it loops missing episodes and auto-grabs each (no pack grab)
    assert "st.epMeta[k].state === 'missing'" in _VIEW
    assert ".vdl-season-grab" in _CSS


def test_season_grab_reuses_the_single_grab_path():
    # each episode goes through the same searchInto + _autoPick as a manual Auto,
    # so the batch can't diverge from the single path
    assert 'autoGrabEpisode(container, st, sn, eps[idx++], src)' in _VIEW
    assert 'searchInto(container, panel,' in _VIEW
