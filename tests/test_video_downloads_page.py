"""Downloads page UX overhaul — string-contract level (like test_video_side_shell.py), so a
refactor that silently drops the per-type theming, the sidebar count, or the new statuses
fails here."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-downloads-page.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")


def test_cards_carry_a_type_for_cinema_theming():
    assert "function dlType(" in _JS
    assert "data-vtype" in _JS                       # set on each card
    # the three Cinema palette colours are defined per type
    for vt in ('[data-vtype="movie"]', '[data-vtype="tv"]', '[data-vtype="youtube"]'):
        assert vt in _CSS, vt
    assert "--vt: 79, 143, 247" in _CSS and "--vt: 240, 69, 75" in _CSS   # azure + red


def test_importing_is_a_first_class_active_status():
    assert "importing:" in _JS and "import_failed:" in _JS
    assert "s === 'importing'" in _JS                # counts as active
    assert "vdpg-prog-indet" in _JS                  # indeterminate sweep for queued/searching/importing


def test_sidebar_has_a_live_downloads_count():
    assert "data-video-downloads-badge" in _INDEX     # the nav badge element
    assert "function setDownloadsBadge(" in _JS
    assert "function badgePoll(" in _JS               # stays live off-page too


def test_cards_expand_into_a_detail_drawer():
    assert "function drawerHTML(" in _JS and "function renderDrawer(" in _JS
    assert "_expanded" in _JS                          # open state survives re-patches
    assert "vdpg-dr-cast" in _JS and "vdpg-dr-syn" in _JS   # cast + synopsis sections
    assert "data-vdpg-copy" in _JS                     # copy-path action
    # the lazy TMDB detail endpoint the drawer fetches synopsis/cast from
    assert "/downloads/meta/" in _JS


def test_drawer_renders_the_rich_tmdb_fields():
    # cast PHOTOS (the bug was the wrong field name) + logo header + trailer + providers
    assert "c.photo" in _JS                            # correct TMDB cast-photo field
    assert "vdpg-dr-logo" in _JS                       # title logo header
    assert "vdpg-dr-trailer" in _JS and "vdpg-prov" in _JS   # trailer + where-to-watch
    assert "trailer_url" in _JS and "providers" in _JS


def test_drawer_has_episode_youtube_and_availability_blocks():
    assert "vdpg-dr-ytthumb" in _JS                     # youtube big thumbnail header
    assert "vdpg-dr-ep" in _JS and "vdpg-dr-epstill" in _JS   # episode still + block
    assert "ctx.peer" in _JS                           # grab-time availability snapshot
    assert "yt-meta" in _JS                            # youtube metadata fetch
    assert "season=" in _JS and "episode=" in _JS      # episode params on the meta fetch


def test_download_meta_routes_are_registered():
    import api.video as videoapi
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/video/downloads/meta/<kind>/<int:tmdb_id>" in rules
    assert "/api/video/downloads/yt-meta/<video_id>" in rules

