"""What a colon in a title becomes in file and folder names (SeadogsBooty).

file systems refuse ':', so it was always dropped: "Dexter Resurrection".
the setting picks dash, space-dash-space or smart instead; delete stays the
default so a library named the old way doesn't read as misnamed after an update.
"""

import pytest

from core.video import organization as org

EP = {"series": "Dexter: Resurrection", "season": 1, "episode": 1,
      "episode_title": "A Beating Heart", "quality": "WEBDL-1080p", "resolution": "1080p",
      "source": "webdl"}


def _name(mode, fields=EP, scope="episode"):
    settings = org.normalize({"colon_replacement": mode})
    return org.render_path(scope, "/tv", fields, settings, ".mkv")["path"].replace("\\", "/")


@pytest.mark.parametrize("mode,expected", [
    ("delete", "/tv/Dexter Resurrection/Season 01/Dexter Resurrection - S01E01 - A Beating Heart WEBDL-1080p.mkv"),
    ("smart", "/tv/Dexter - Resurrection/Season 01/Dexter - Resurrection - S01E01 - A Beating Heart WEBDL-1080p.mkv"),
    ("space_dash_space", "/tv/Dexter - Resurrection/Season 01/Dexter - Resurrection - S01E01 - A Beating Heart WEBDL-1080p.mkv"),
    ("dash", "/tv/Dexter- Resurrection/Season 01/Dexter- Resurrection - S01E01 - A Beating Heart WEBDL-1080p.mkv"),
])
def test_each_mode_names_dexter_resurrection(mode, expected):
    assert _name(mode) == expected


def test_delete_is_the_default_and_unknown_modes_keep_it():
    assert org.default_settings()["colon_replacement"] == "delete"
    assert org.normalize({"colon_replacement": "bogus"})["colon_replacement"] == "delete"


def test_smart_tells_a_separator_from_a_time():
    assert org.replace_colons("Live at 12:30: The Show", "smart") == "Live at 12-30 - The Show"
    assert org.replace_colons("Title:", "smart").strip() == "Title -"


def test_movies_and_episode_titles_follow_it_too():
    movie = {"title": "Star Wars: Episode IV", "year": 1977, "quality": "Bluray-1080p",
             "resolution": "1080p", "source": "bluray"}
    assert "Star Wars - Episode IV (1977)" in _name("smart", movie, "movie")
    ep = dict(EP, series="Show", episode_title="Part One: The Return")
    assert _name("smart", ep).endswith("Show - S01E01 - Part One - The Return WEBDL-1080p.mkv")


def test_no_colon_left_in_any_mode():
    for mode in org.COLON_MODES:
        assert ":" not in _name(mode)[1:]


def test_the_settings_select_offers_every_mode_and_saves_it():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    select = html[html.index('id="vo-colon-replacement"'):]
    select = select[:select.index("</select>")]
    assert sorted(re.findall(r'value="([a-z_]+)"', select)) == sorted(org.COLON_MODES)
    js = (root / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
    assert "colon_replacement: val('vo-colon-replacement')" in js
    assert "set('vo-colon-replacement', _videoOrg.colon_replacement" in js
