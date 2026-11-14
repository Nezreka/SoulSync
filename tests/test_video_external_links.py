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
_LIST = (_ROOT / "webui" / "src" / "features" / "video-library" / "episode-list.tsx").read_text(
    encoding="utf-8")   # a show's season and its rows are React now


def test_title_level_links_still_present():
    assert "https://www.imdb.com/title/' + d.imdb_id" in _JS
    assert "https://www.themoviedb.org/' + (d.kind === 'movie' ? 'movie' : 'tv')" in _JS
    assert "https://thetvdb.com/?id=' + d.tvdb_id" in _JS


def test_letterboxd_is_added_for_movies_only():
    assert "https://letterboxd.com/tmdb/' + d.tmdb_id" in _JS
    # gated to films
    assert "d.kind === 'movie' && d.tmdb_id" in _JS


def test_per_episode_deeplinks():
    # the opened episode is the React list's (episode-list.tsx) since Nov 2026
    assert "https://www.themoviedb.org/tv/${show.tmdbId}/season/${season}/episode/${episode.episode_number}" in _LIST
    # IMDb episode link when the id is known
    assert "extra.imdb_id" in _LIST and "https://www.imdb.com/title/${extra.imdb_id}/" in _LIST
    # rendered into the episode expand panel + styled
    assert 'className="vd-ep-links"' in _LIST
    assert ".vd-ep-links" in _CSS


def test_tmdb_preview_items_keep_everything_in_app():
    # both the title block and the episode links suppress for source==tmdb
    assert "d.source === 'tmdb'" in _JS
    assert "show.source === 'tmdb'\n      ? []" in _LIST
