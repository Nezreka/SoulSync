"""a band named with "&" keeps its folder and album artist tag (#2131).

wolf39us: Preview Rename / Organize moved "Above & Beyond/2015 - We Are All
We Need" to "Above/...". the album's artist is the library's one artist
"Above & Beyond", but the track carried phantom "Above" and "Beyond"
credits (and the library held both as artists), so 'first listed' read the
band's name as a list and kept its first word.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import core.imports.paths as import_paths
from core.imports.collab_artist import collab_primary
from core.imports.paths import build_final_path_for_track
from core.library_reorganize import _build_post_process_context
from core.metadata import source as src_module

TEMPLATE = "$albumartist/$year - $album/$track - $title"
BAND = "Above & Beyond"
LIBRARY = {"above & beyond": True, "above": True, "beyond": True, "mat zo": True}


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _cfg(monkeypatch, tmp_path, identified=(BAND,)):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": TEMPLATE, "single_path": TEMPLATE, "compilation_path": TEMPLATE,
        },
        "file_organization.collab_artist_mode": "first",
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(import_paths, "library_has_artist", lambda n: n.casefold() in LIBRARY)
    known = {n.casefold() for n in identified}
    monkeypatch.setattr(import_paths, "library_has_identified_artist",
                        lambda n: n.casefold() in known)


def _reorganize_folder(tmp_path, album_artist, credits):
    """the context the reorganize planner builds for one library track."""
    context = _build_post_process_context(
        {"id": "", "name": "We Are All We Need", "release_date": "2015-01-16",
         "album_type": "album", "total_tracks": 16},
        {"name": "Blue Sky Action", "track_number": 3, "disc_number": 1,
         "artists": [{"name": n} for n in credits]},
        album_artist, "We Are All We Need", 1, record_type="album")
    context["_no_album_folder_reuse"] = True
    path, _ = build_final_path_for_track(
        context, context["spotify_artist"],
        {"is_album": True, "album_name": "We Are All We Need", "track_number": 3,
         "disc_number": 1, "album_type": "album"},
        ".flac", create_dirs=False)
    return os.path.relpath(path, tmp_path).split(os.sep)


def test_phantom_word_credits_never_cut_the_band_folder(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    parts = _reorganize_folder(tmp_path, BAND, [BAND, "Above", "Beyond", "Alex Vargas"])
    assert parts[0] == BAND
    # the track names only the words, the library still knows the band
    assert _reorganize_folder(tmp_path, BAND, ["Above", "Beyond"])[0] == BAND


def test_tom_petty_and_the_heartbreakers(monkeypatch, tmp_path):
    band = "Tom Petty & The Heartbreakers"
    _cfg(monkeypatch, tmp_path, identified=(band,))
    assert _reorganize_folder(tmp_path, band, [band, "Tom Petty"])[0] == band


def test_a_real_collab_credit_still_files_under_its_first_artist(monkeypatch, tmp_path):
    """the library knows the two artists, not the combined credit."""
    _cfg(monkeypatch, tmp_path, identified=("grandson", "K.Flay"))
    assert _reorganize_folder(tmp_path, "grandson & K.Flay", ["grandson"])[0] == "grandson"


def test_the_rule_itself():
    knows = {"above & beyond"}.__contains__

    def lookup(name):
        return knows(name.casefold())

    assert collab_primary(BAND, [{"name": BAND}], [{"name": "Above"}],
                          known_names=["Above", "Beyond"], library_has=lambda n: True,
                          library_knows=lookup) == ""
    # a structured list still wins: the provider said who leads
    assert collab_primary("Above & Beyond, Mat Zo", [{"name": BAND}, {"name": "Mat Zo"}],
                          library_knows=lookup) == BAND
    # ";" is never part of an act's name
    assert collab_primary("Above & Beyond; Mat Zo", None, None,
                          library_knows=lambda n: True) == BAND


def test_the_album_artist_tag_keeps_the_band():
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    context = {
        "original_search_result": {"title": "Blue Sky Action",
                                   "artists": [{"name": "Above"}, {"name": "Beyond"}]},
        "spotify_album": {"id": "1", "name": "We Are All We Need", "artists": [{"name": BAND}]},
        "source": "deezer",
    }
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", lambda n: n.casefold() in LIBRARY), \
            patch.object(src_module, "library_has_identified_artist",
                         lambda n: n.casefold() == BAND.casefold()):
        meta = src_module.extract_source_metadata(
            context, {"name": BAND}, {"album_name": "We Are All We Need"})
    assert meta["album_artist"] == BAND
