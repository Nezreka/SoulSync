"""Continue Watching P2 — detail-page UI seams (pin tests).

The backend (schema v45) ships per-episode watch state + a next_up slot; this
pins the frontend wiring: episode rows show watched checks / progress bars /
the Next-up highlight, the hero CTA deep-links the next episode, and the page
opens on the season you're actually in.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
    encoding="utf-8", errors="replace")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(
    encoding="utf-8", errors="replace")


class TestEpisodeRows:
    def test_watched_check_and_classes(self):
        assert 'vd-ep-check' in _JS
        assert "if (ep.watched) owned += ' vd-ep--watched'" in _JS

    def test_progress_bar_only_for_in_progress(self):
        # in-progress = started but NOT watched, and runtime known (else no %)
        assert '!ep.watched && (ep.view_offset_ms || 0) > 0 && ep.runtime_minutes' in _JS
        assert 'vd-ep-prog-fill' in _JS

    def test_next_up_highlight_matches_payload_slot(self):
        assert 'nu.season_number === selectedSeason' in _JS
        assert 'nu.episode_number === ep.episode_number' in _JS
        assert 'vd-ep-next-chip' in _JS


class TestHeroCta:
    def test_deep_links_next_episode(self):
        assert "(snu && d.server.episode_url) ? d.server.episode_url : d.server.url" in _JS

    def test_resume_labels(self):
        assert "verb = snu.resume ? 'Resume' : 'Play'" in _JS
        # movies: resume only when in progress and not already watched
        assert "d.kind === 'movie' && !d.watched && (d.view_offset_ms || 0) > 0" in _JS

    def test_watched_tags_in_meta(self):
        assert _JS.count('vd-watched-tag') >= 3   # show full/partial + movie


class TestSeasonLanding:
    def test_initial_season_prefers_next_up(self):
        assert 'function initialSeasonNum(d)' in _JS
        # both load paths (initial open + the post-sync refresh) go through it
        assert _JS.count('selectedSeason = initialSeasonNum(d)') == 2
        # ...and never a season the payload doesn't have
        assert 's.season_number === nu.season_number' in _JS


def test_css_carries_the_watch_state_styles():
    for cls in ('.vd-ep-prog', '.vd-ep-prog-fill', '.vd-ep-check',
                '.vd-ep--watched', '.vd-ep--next', '.vd-ep-next-chip',
                '.vd-watched-tag', '.vd-play-ep'):
        assert cls in _CSS, f"missing style {cls}"
