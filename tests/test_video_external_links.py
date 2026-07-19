"""Deep-linked external metadata on video detail (#1039, QT3496).

Movies/shows already deep-linked to IMDb/TMDB/TVDB; this adds Letterboxd
(film-only, via TMDB id) and per-EPISODE links (TMDB episode page from the
show's tmdb id + season/episode, IMDb when the episode id is known).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_title_level_links_still_present():
    assert "https://www.imdb.com/title/' + d.imdb_id" in _JS
    assert "https://www.themoviedb.org/' + (d.kind === 'movie' ? 'movie' : 'tv')" in _JS
    assert "https://thetvdb.com/?id=' + d.tvdb_id" in _JS


def test_letterboxd_is_added_for_movies_only():
    assert "https://letterboxd.com/tmdb/' + d.tmdb_id" in _JS
    # gated to films
    assert "d.kind === 'movie' && d.tmdb_id" in _JS


def test_per_episode_deeplinks():
    assert "function episodeLinks(showTmdb, season, episode, ex)" in _JS
    assert "https://www.themoviedb.org/tv/' + showTmdb + '/season/' + season + '/episode/' + episode" in _JS
    # IMDb episode link when the id is known
    assert "ex && ex.imdb_id" in _JS
    # rendered into the episode expand panel + styled
    assert "episodeLinks(showTmdb, season, episode, ex)" in _JS
    assert ".vd-ep-links" in _CSS


def test_tmdb_preview_items_keep_everything_in_app():
    # both the title block and the episode links suppress for source==tmdb
    assert "d.source === 'tmdb'" in _JS
    assert "if (data && data.source === 'tmdb') return '';" in _JS
