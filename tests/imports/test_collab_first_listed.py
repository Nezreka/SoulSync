"""'first listed artist' when the source names the collab as one string.

grandson & K.Flay - Peaches landed in "grandson & K.Flay/Single/..." with
collab mode on first listed (discord, SeadogsBooty). the album artist was
the combined string and the track list held only "grandson", and the old
rule only split a list of 2+.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import core.imports.paths as import_paths
from core.imports.collab_artist import first_listed_artist, listed_primary_of
from core.imports.paths import build_final_path_for_track
from core.metadata import source as src_module

TEMPLATE = "$albumartist/$albumtype/[$year] $album/$track - $title"
COMBINED = "grandson & K.Flay"


def test_lone_listed_name_counts_when_the_combined_name_starts_with_it():
    assert listed_primary_of(COMBINED, {"name": "grandson"}) == "grandson"
    assert listed_primary_of("grandson, VIC MENSA & Masked Wolf", "grandson") == "grandson"
    assert listed_primary_of("Colbie Caillat feat. Lee Brice", "Colbie Caillat") == "Colbie Caillat"
    assert listed_primary_of("Ten Years x Someone", "Ten Years") == "Ten Years"


def test_a_band_named_with_an_ampersand_stays_whole():
    assert listed_primary_of("Simon & Garfunkel", "Simon & Garfunkel") == ""
    assert first_listed_artist("Simon & Garfunkel", [{"name": "Simon & Garfunkel"}]) == ""
    # a name that merely starts the same isn't the primary
    assert listed_primary_of("Lil Nas X", "Lil Nas") == ""
    assert listed_primary_of("Earthgang & Wale", "Earth") == ""


def test_a_list_of_several_still_takes_the_first():
    assert first_listed_artist("whatever", [{"name": "JAY-Z"}, {"name": "Kanye West"}]) == "JAY-Z"
    assert first_listed_artist(COMBINED, []) == ""


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _cfg(monkeypatch, tmp_path, mode="first"):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": TEMPLATE, "single_path": TEMPLATE, "compilation_path": TEMPLATE,
        },
        "file_organization.collab_artist_mode": mode,
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    return config


def _folder(tmp_path, artist_ctx, album_artists, track_artists, source):
    context = {
        "artist": artist_ctx,
        "album": {"name": "Peaches", "id": "a1", "release_date": "2020-01-01",
                  "total_tracks": 1, "album_type": "single", "artists": album_artists},
        "track_info": {"name": "Peaches", "id": "t1", "track_number": 1,
                       "disc_number": 1, "artists": track_artists},
        "original_search_result": {"title": "Peaches", "clean_title": "Peaches",
                                   "clean_album": "Peaches", "artists": track_artists},
        "source": source, "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, artist_ctx,
        {"is_album": True, "album_name": "Peaches", "track_number": 1,
         "disc_number": 1, "album_type": "single"},
        ".mp3", create_dirs=False)
    return os.path.relpath(path, tmp_path).split(os.sep)[0]


def test_auto_import_shape_files_under_the_primary(monkeypatch, tmp_path):
    """auto-import puts the combined name on the artist and album, the track
    names only the primary."""
    _cfg(monkeypatch, tmp_path)
    combined = {"name": COMBINED, "id": "324922"}
    assert _folder(tmp_path, combined, [combined], [{"name": "grandson"}], "deezer") == "grandson"


def test_album_path_template_too(monkeypatch, tmp_path):
    """an uncustomized single_path routes singles through album_path, where
    the album's own list ([combined]) is what the rule sees first."""
    config = _cfg(monkeypatch, tmp_path)
    config._values["file_organization.templates"] = {"album_path": TEMPLATE}
    combined = {"name": COMBINED}
    assert _folder(tmp_path, combined, [combined], [{"name": "grandson"}], "deezer") == "grandson"


def test_full_mode_keeps_the_combined_folder(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, mode="full")
    combined = {"name": COMBINED}
    assert _folder(tmp_path, combined, [combined], [{"name": "grandson"}], "deezer") == COMBINED


def test_itunes_album_artist_id_resolves_when_the_context_has_none(monkeypatch, tmp_path):
    """itunes names a collab as one string everywhere; the album's artist id
    is the primary's, and the download context's artist often has no id."""
    _cfg(monkeypatch, tmp_path)
    looked_up = []

    class _Itunes:
        def resolve_primary_artist(self, aid):
            looked_up.append(aid)
            return "grandson"

    monkeypatch.setattr(import_paths, "_get_itunes_client", lambda: _Itunes())
    combined = {"name": COMBINED}
    album_artist = {"name": COMBINED, "id": "1234567"}
    assert _folder(tmp_path, combined, [album_artist], [combined], "itunes") == "grandson"
    assert looked_up == ["1234567"]


def test_a_deezer_album_artist_id_is_never_sent_to_itunes(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    itunes = MagicMock()
    monkeypatch.setattr(import_paths, "_get_itunes_client", lambda: itunes)
    combined = {"name": COMBINED}
    album_artist = {"name": COMBINED, "id": "324922"}
    assert _folder(tmp_path, combined, [album_artist], [combined], "deezer") == COMBINED
    itunes.resolve_primary_artist.assert_not_called()


def test_album_artist_tag_follows_the_same_rule():
    """the tag is what the media server builds the library artist from."""
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    context = {
        "original_search_result": {"title": "Peaches", "artists": [{"name": "grandson"}]},
        # a real album id, as auto-import carries: the #1567 distrust guard
        # stays out of it
        "spotify_album": {"id": "133406982", "name": "Peaches", "artists": [{"name": COMBINED}]},
        "source": "deezer",
    }
    with patch.object(src_module, "get_config_manager", return_value=cfg):
        meta = src_module.extract_source_metadata(
            context, {"name": COMBINED}, {"album_name": "Peaches"})
    assert meta["album_artist"] == "grandson"


def test_web_server_copy_of_the_replacer_agrees(monkeypatch):
    """web_server keeps its own template replacer for the m3u folder."""
    import web_server

    monkeypatch.setattr(web_server, "config_manager",
                        _Config({"file_organization.collab_artist_mode": "first"}))
    ctx = {"artist": "grandson", "albumartist": COMBINED, "album": "Peaches",
           "title": "Peaches", "track_number": 1, "disc_number": 1, "year": "2020",
           "_artists_list": [{"name": COMBINED}], "_track_artists_list": [{"name": "grandson"}]}
    assert web_server._apply_path_template("$albumartist/$album", ctx) == "grandson/Peaches"
