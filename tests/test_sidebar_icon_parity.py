"""Thibs: the music and video sidebars used different icons for the same pages
(the video Automations even wore the Settings gear), different section names
(Collect / Manage vs Find / Video), and Sample Studio sat under System. One
page, one icon, on both sides."""

import re
from pathlib import Path

INDEX = (Path(__file__).resolve().parent.parent / "webui" / "index.html").read_text(encoding="utf-8")


def _icon(attr):
    """the svg body of the nav button carrying this attribute"""
    i = INDEX.index(attr)
    a = INDEX.index("<svg", i)
    return re.sub(r"\s+", " ", INDEX[a:INDEX.index("</svg>", a)])


def _titles(nav_start, nav_end):
    block = INDEX[INDEX.index(nav_start):INDEX.index(nav_end)]
    return re.findall(r'nav-section-title">([^<]+)<', block)


def test_shared_pages_wear_the_same_icon_on_both_sides():
    for music, video in (('data-page="dashboard"', 'data-video-page="video-dashboard"'),
                         ('data-page="search"', 'data-video-page="video-search"'),
                         ('data-page="automations"', 'data-video-page="video-automations"'),
                         ('data-page="chat"', 'data-video-page="video-chat"'),
                         ('data-page="tools"', 'data-video-page="video-tools"'),
                         ('data-page="settings"', 'data-video-page="video-settings"')):
        assert _icon(music) == _icon(video), (music, video)


def test_automations_is_not_the_settings_gear():
    assert _icon('data-video-page="video-automations"') != _icon('data-video-page="video-settings"')


def test_both_sides_have_the_same_sections():
    music = _titles('data-page="dashboard"', 'data-video-page="video-dashboard"')
    video = _titles('data-video-page="video-dashboard"', 'data-video-page="video-help"')
    assert music == video == ["Collect", "Manage", "System"]


def test_sample_studio_is_with_the_library_tools_not_system():
    manage = INDEX.index('data-section="music"')
    system = INDEX.index('data-section="system"')
    assert manage < INDEX.index('data-page="sample-studio"') < system


def test_a_short_screen_scrolls_the_whole_drawer():
    """Thibs, landscape phone: the drawer header filled most of the screen and
    the nav below it had nowhere to scroll. measured in chromium at 740x360:
    the inner scroller had 196px; now the drawer scrolls with the full 359."""
    css = (Path(__file__).resolve().parent.parent / "webui" / "static" / "mobile.css").read_text(encoding="utf-8")
    block = css[css.index("@media (max-height: 500px) {"):]
    block = block[:block.index("\n}\n")]
    sidebar = block[block.index("    .sidebar {"):]
    assert "overflow-y: auto" in sidebar[:sidebar.index("}")]
    scroll = block[block.index("    .sidebar-scroll {"):]
    assert "overflow: visible" in scroll[:scroll.index("}")]
