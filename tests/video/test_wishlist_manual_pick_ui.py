"""LiveLeak failing-hub follow-up: every video wishlist item gets BOTH search
actions, Sonarr-style — auto ("Search now", the system picks) AND manual (opens
the shared release-picker modal so the USER picks). The wishlist is React now
(webui/src/routes/wishlist); its behaviour is run in -video.route.test.tsx.
These are the source guards that were here, pointed at the new files.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_WLD = _ROOT / "webui" / "src" / "routes" / "wishlist"
_SHEETS = (_WLD / "-ui" / "video-sheets.tsx").read_text(encoding="utf-8")
_ACTIONS = (_WLD / "-video.use-actions.ts").read_text(encoding="utf-8")
_PAGE = (_WLD / "-ui" / "video-wishlist.tsx").read_text(encoding="utf-8")
_DOWNLOAD_JS = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")


def test_manual_pick_rendered_on_all_three_surfaces():
    # the movie panel, a season's menu and an episode's menu each offer the pick
    assert "actions.pickMovie(movie)" in _SHEETS
    assert "actions.pickEpisodes(show, season.season_number, null)" in _SHEETS
    assert "actions.pickEpisodes(show, season.season_number, ep.episode_number)" in _SHEETS
    assert _SHEETS.count("label: 'Pick a release'") == 2   # season + episode menus


def test_manual_pick_opens_the_shared_release_picker():
    assert "window.VideoDownload?.manualSearch" in _ACTIONS
    # and says so when the modal isn't loaded instead of doing nothing
    assert "toast('Manual search unavailable', 'error')" in _ACTIONS


def test_modal_supports_movie_scope():
    # header label handles movies (was "Season undefined") and the search carries the year
    assert "scope === 'movie'" in _DOWNLOAD_JS
    assert "year: opts.year || null" in _DOWNLOAD_JS


def test_pick_and_auto_are_separate_controls():
    # the auto Search is a button on the row; the manual pick sits in the row's
    # menu, so the two can never overlap the way the old absolutely-placed icons could.
    assert "aria-label={`Search ${label} now`}" in _SHEETS
    assert "<MoreMenu label={`More for ${title}`} items={more} />" in _SHEETS


def test_failing_filter_chip_wired():
    # the fix-it hub: a "⚠ Failing" chip filters the wishlist to stuck items,
    # each carrying auto + manual + remove — the same chip as the music side.
    assert "wlp-chip wlp-chip--warn" in _PAGE
    assert "failingOnlyMovies(movies)" in _PAGE and "failingOnlyShows(shows)" in _PAGE
    assert "onClick={() => onFailing(!failing)}" in _PAGE
