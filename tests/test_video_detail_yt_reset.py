"""Regression: a YouTube channel's playlists must not leak onto the next movie/show.

YouTube channels/playlists render into the SHOW-detail DOM (they reuse that page).
The playlist section is only in the show DOM, but a later movie/show load runs with
q() scoped to a different root — so the reset has to target the show subpage
directly AND run on every detail load (via resetExtras), or stale playlists show.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DETAIL = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def test_playlist_section_lives_only_in_show_detail():
    # The section markup is in the show subpage; the movie subpage must not have it.
    show_start = _INDEX.index('data-video-detail="show"')
    movie_start = _INDEX.index('data-video-detail="movie"')
    show_block = _INDEX[show_start:movie_start]
    movie_block = _INDEX[movie_start:movie_start + 4000]
    assert 'data-vd-yt-pl-section' in show_block
    assert 'data-vd-yt-pl-section' not in movie_block


def test_reset_targets_the_show_dom_directly():
    # ytResetPlaylists must NOT rely on the kind-scoped q() (which points at the
    # movie root during a movie load) — it queries the show subpage explicitly.
    i = _DETAIL.index('function ytResetPlaylists(')
    body = _DETAIL[i:i + 700]
    assert "document.querySelector('[data-video-detail=\"show\"]')" in body
    assert 'data-vd-yt-playlists' in body


def test_every_detail_load_clears_stale_playlists():
    # resetExtras() runs on loadMovie/loadShow/loadChannel/loadPlaylist, so wiring
    # the clear there covers all navigations into a non-YouTube detail.
    i = _DETAIL.index('function resetExtras(')
    body = _DETAIL[i:i + 700]
    assert 'ytResetPlaylists()' in body
