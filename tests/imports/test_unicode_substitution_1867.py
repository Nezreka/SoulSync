"""windows-safe lookalikes instead of underscores in names (#1867, QT3496).

off (every existing install): AC/DC files as AC_DC, byte for byte as before.
on: AC∕DC, What's Going On？, DAMN․ and both sanitizers agree, so a download
and reorganize land in the same place.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.paths import sanitize_filename, substitute_lookalikes

_ROOT = Path(__file__).resolve().parents[2]


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture()
def cfg(monkeypatch):
    config = _Config({})
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    return config


@pytest.mark.parametrize("raw, want", [
    ("AC/DC", "AC∕DC"),
    ("What's Going On?", "What's Going On？"),
    ("Volume 1: The Hits", "Volume 1꞉ The Hits"),
    ("DAMN.", "DAMN․"),
    ("Fred again..", "Fred again․․"),
    ('Say "Hi" (the "Mix")', "Say “Hi” (the “Mix”)"),
    ("a\\b*c|d<e>f", "a⧵b⁎c│d‹e›f"),
])
def test_lookalikes(raw, want):
    assert substitute_lookalikes(raw) == want


def test_kept_dot_is_not_a_wide_glyph():
    # #1873: the fullwidth stop drew as ". " so "DAMN．" looked like a stray
    # period and space. the kept dot has to be as narrow as a real one.
    dot = substitute_lookalikes("DAMN.")[-1]
    assert dot != "."
    assert unicodedata.east_asian_width(dot) not in ("F", "W")


def test_off_keeps_underscores_byte_for_byte(cfg):
    assert sanitize_filename("AC/DC") == "AC_DC"
    assert sanitize_filename("DAMN.") == "DAMN"
    assert sanitize_filename("What's Going On?") == "What's Going On_"


def test_on_keeps_the_symbols(cfg):
    cfg.values["file_organization.unicode_substitution"] = True
    assert sanitize_filename("AC/DC") == "AC∕DC"
    assert sanitize_filename("DAMN. ") == "DAMN․"
    # leading dots still go: a dotfile folder vanishes from media servers (#1129)
    assert sanitize_filename("...Baby One More Time") == "Baby One More Time"
    assert sanitize_filename("...") == "_"
    assert sanitize_filename("CON") == "_CON"


def test_web_server_copy_agrees(cfg):
    # web_server keeps its own sanitizer for template paths; both must agree
    # or a download and reorganize disagree on the folder.
    import web_server
    for value in (False, True):
        cfg.values["file_organization.unicode_substitution"] = value
        for raw in ("AC/DC", "DAMN.", 'Say "Hi"', "Volume 1: The Hits"):
            assert web_server._sanitize_filename(raw) == sanitize_filename(raw)


def test_the_built_path_keeps_the_symbols(cfg, tmp_path, monkeypatch):
    cfg.values.update({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {"album_path": "$albumartist/$album/$track - $title"},
        "file_organization.unicode_substitution": True,
    })
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    artist, album, title = "AC/DC", "Who Made Who?", "D.T."
    context = {
        "artist": {"name": artist},
        "album": {"name": album, "id": "a1", "release_date": "1986-05-24",
                  "total_tracks": 9, "album_type": "album",
                  "artists": [{"name": artist}]},
        "track_info": {"name": title, "id": "t", "track_number": 3,
                       "disc_number": 1, "artists": [{"name": artist}]},
        "original_search_result": {"title": title, "clean_title": title,
                                   "clean_album": album, "clean_artist": artist,
                                   "artists": [{"name": artist}]},
        "source": "spotify", "is_album_download": True,
    }
    path, _ = import_paths.build_final_path_for_track(
        context, {"name": artist},
        {"is_album": True, "album_name": album, "track_number": 3, "disc_number": 1},
        ".flac", create_dirs=False)
    rel = Path(path).relative_to(tmp_path).parts
    assert rel == ("AC∕DC", "Who Made Who？", "03 - D.T․.flac")


def test_off_does_not_move_the_naming_fingerprint(monkeypatch):
    # every settings save posts the whole section, so the new key shows up as
    # false for everyone. that must not unsettle their placed albums.
    import core.settings as settings
    values = {"file_organization": {"templates": {"album_path": "$albumartist/$album"}}}
    monkeypatch.setattr(settings.config_manager, "get",
                        lambda key, default=None: values.get(key, default))
    before = lr.naming_fingerprint()
    values["file_organization"] = {**values["file_organization"], "unicode_substitution": False}
    assert lr.naming_fingerprint() == before
    values["file_organization"]["unicode_substitution"] = True
    assert lr.naming_fingerprint() != before


def test_settings_ui_round_trip_wiring():
    js = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'id="unicode-substitution"' in html
    assert "unicode_substitution: document.getElementById('unicode-substitution').checked" in js
    assert "settings.file_organization?.unicode_substitution === true" in js
