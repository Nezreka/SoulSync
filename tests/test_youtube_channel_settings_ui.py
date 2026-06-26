"""Per-channel settings modal — UI wiring (string-contract level, like the other video-JS
tests). The functional core (storage/API/download wiring) is in test_youtube_channel_settings.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_YT = (_ROOT / "webui" / "static" / "video" / "video-youtube.js").read_text(encoding="utf-8")
_WL = (_ROOT / "webui" / "static" / "video" / "video-watchlist.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_channel_and_playlist_cards_both_have_a_settings_cog():
    assert 'data-vyt-wsettings' in _WL          # cog button on the watchlist cards
    assert _WL.count('vyt-wcard-cog') >= 2      # channel + playlist cards
    assert '.vyt-wcard-cog' in _CSS
    assert 'data-kind="channel"' in _WL and 'data-kind="playlist"' in _WL


def test_cog_click_opens_the_settings_modal_with_kind():
    assert 'VideoYoutube.openChannelSettings(' in _WL
    assert "cog.getAttribute('data-vyt-wsettings')" in _WL
    assert "cog.getAttribute('data-kind')" in _WL          # kind passed through
    # the modal reads the kind to label channel vs playlist
    assert "kind === 'playlist' ? 'Playlist' : 'Channel'" in _YT


def test_modal_is_exposed_and_built():
    assert 'openChannelSettings: openChannelSettings' in _YT
    assert 'function openChannelSettings(' in _YT
    assert '.vyt-cset-overlay' in _CSS          # modal styles exist


def test_modal_loads_and_saves_via_the_settings_api():
    assert "/channel/' + encodeURIComponent(channelId) + '/settings'" in _YT
    assert "method: 'POST'" in _YT
    # the form carries the custom name + the optional quality override
    assert 'data-cset-name' in _YT and 'data-cset-qon' in _YT
    assert 'custom_name' in _YT and 'max_resolution' in _YT
