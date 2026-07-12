"""Search page — YouTube channel results as native poster tiles.

Boulder: the channel results felt disconnected (own card component, own grid,
buried under People). Now they render as the SAME .vsr-card poster tile the
movie/TV results use (shared .vsr-grid, hover-lift, peek), avatar centered on a
YouTube-branded ground, and the group sits right after TV Shows (before People).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCH_JS = (_ROOT / "webui" / "static" / "video" / "video-search.js").read_text(encoding="utf-8")
_YT_JS = (_ROOT / "webui" / "static" / "video" / "video-youtube.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_channel_card_reuses_the_vsr_poster_tile():
    card = _YT_JS.split("function channelResultCard")[1].split("function resolve")[0]
    assert "vsr-card vsr-card--yt" in card          # the shared card class
    assert "vsr-poster vsr-poster--yt" in card       # the 2:3 poster tile
    assert "vsr-ribbon vsr-ribbon--yt" in card       # ribbon in the In-Library/Preview slot
    assert "vsr-peek" in card                        # same hover 'i' affordance
    assert "vsr-name" in card and "vsr-sub" in card  # same info block as TV cards
    assert "data-vyt-open-channel" in card           # still opens the channel page
    # the old bespoke component is gone
    assert "vyt-result" not in card


def test_channels_render_in_the_shared_grid_after_tv_before_people():
    # the group builder uses the shared .vsr-grid, not a bespoke grid
    ytg = _SEARCH_JS.split("function ytGroup")[1].split("function render")[0]
    assert "vsr-grid" in ytg and "vyt-result-grid" not in ytg
    # render order: movie, show, YT, person
    order = _SEARCH_JS.split("var html =")[1].split(";")[0]
    i_show = order.index("byKind.show")
    i_yt = order.index("ytGroup(")
    i_person = order.index("byKind.person")
    assert i_show < i_yt < i_person, "channels must sit after TV Shows, before People"


def test_skeleton_uses_poster_shaped_tiles_in_the_shared_grid():
    skel = _SEARCH_JS.split("function ytSkeletonGroup")[1].split("function tmdbGroup")[0]
    assert "vsr-card" in skel and "vsr-poster" in skel
    assert "vsr-grid" in skel and "vyt-result-grid" not in skel


def test_branded_poster_css_exists_and_old_component_removed():
    assert ".vsr-poster--yt" in _CSS
    assert ".vsr-yt-av" in _CSS
    assert ".vsr-ribbon--yt" in _CSS
    # the dead search-only component is gone
    assert ".vyt-result-grid {" not in _CSS
    assert ".vyt-result-art {" not in _CSS
