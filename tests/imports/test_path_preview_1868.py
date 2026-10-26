"""#1868 (QT3496): a live preview under each naming template.

the settings page shows what a template makes before it's saved. the preview
renders through the importer's own path builder against a made-up release, so
the thing worth pinning is that it agrees with a real import, reads the form's
unsaved toggles without saving them, and never asks the library about the
made-up artists.
"""

from __future__ import annotations

import os

import pytest

import core.imports.artist_facts as artist_facts_mod
import core.imports.path_preview as path_preview
import core.imports.paths as import_paths
import core.settings
from core.imports.path_preview import SCENARIOS, preview_paths
from core.imports.paths import auto_disc_folder, build_final_path_for_track, preview_settings


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def saved(monkeypatch, tmp_path):
    """the saved config the preview layers over; facts lookups must not happen."""
    values = {
        "soulseek.transfer_path": str(tmp_path / "T"),
        "file_organization.enabled": True,
        "file_organization.templates": {},
        "file_organization.collab_artist_mode": "first",
        "file_organization.auto_disambiguation": True,
        "file_organization.disc_label": "Disc",
        "file_organization.unicode_substitution": False,
    }
    monkeypatch.setattr(core.settings, "config_manager", _Config(values))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    def no_library(*_a, **_kw):
        raise AssertionError("the preview asked the library about a made-up artist")
    monkeypatch.setattr(artist_facts_mod, "artist_facts", no_library)
    return values


def _import_destination(saved, root, album_path, disc_label="Disc"):
    """what a real import of the preview's multi-disc release writes."""
    saved["file_organization.templates"] = {"album_path": album_path}
    saved["file_organization.disc_label"] = disc_label
    context = {
        "artist": {"name": "The Beatles"},
        "album": {"name": "The Beatles", "id": "alb", "release_date": "1968-11-22",
                  "total_tracks": 30, "total_discs": 2, "total_discs_declared": True,
                  "album_type": "album", "artists": [{"name": "The Beatles"}],
                  "disambiguation": "2009 remaster", "label": "Apple", "explicit": False},
        "track_info": {"name": "Birthday", "id": "t", "track_number": 1, "disc_number": 2,
                       "artists": [{"name": "The Beatles"}], "explicit": False},
        "original_search_result": {"title": "Birthday", "clean_title": "Birthday",
                                   "clean_album": "The Beatles", "clean_artist": "The Beatles",
                                   "artists": [{"name": "The Beatles"}]},
        "source": "deezer", "is_album_download": True,
        "_audio_quality": "FLAC 16bit",
    }
    path, _ = build_final_path_for_track(
        context, {"name": "The Beatles"},
        {"is_album": True, "album_name": "The Beatles", "track_number": 1, "disc_number": 2},
        ".flac", create_dirs=False)
    return os.path.relpath(path, root).replace(os.sep, "/")


@pytest.mark.parametrize("album_path,disc_label", [
    ("$albumartist/$albumartist - $album/$track - $title", "Disc"),
    ("$albumartist/[$year] $album $contentrating/$track - $title [$quality]", "CD"),
    ("$albumartist/$album/$disc-$track $title", "Disc"),
    ("$albumartistletter/$albumartist/$album/Disc $discnum/$track - $title", "CD"),
])
def test_preview_is_what_the_importer_writes(saved, tmp_path, album_path, disc_label):
    real = _import_destination(saved, tmp_path / "T", album_path, disc_label)
    # the saved config says otherwise: the preview has to use the form's values
    saved["file_organization.templates"] = {"album_path": "Wrong/$title"}
    saved["file_organization.disc_label"] = "Wrong"
    preview = preview_paths({"album_path": album_path}, {"disc_label": disc_label}, "multidisc")
    assert preview["paths"]["album_path"] == real


def test_disc_label_previews_a_multi_disc_album_whatever_the_scenario(saved):
    out = preview_paths({"album_path": "$albumartist/$album/$track - $title"},
                        {"disc_label": "CD"}, "standard")
    assert "/CD 2/" in out["paths"]["disc_label"]
    assert out["disc_label_used"] is True
    assert "/CD " not in out["paths"]["album_path"]


def test_template_that_places_the_disc_says_the_label_is_unused(saved):
    out = preview_paths({"album_path": "$albumartist/$album/${disc}-$track $title"},
                        {"disc_label": "CD"}, "multidisc")
    assert out["disc_label_used"] is False
    assert "CD 2" not in out["paths"]["disc_label"]


def test_unsaved_toggles_render_and_never_stick(saved):
    on = preview_paths({"album_path": "$albumartist/$album/$title"},
                       {"unicode_substitution": True}, "explicit")
    off = preview_paths({"album_path": "$albumartist/$album/$title"},
                        {"unicode_substitution": False}, "explicit")
    assert on["paths"]["album_path"] == "Kendrick Lamar/DAMN․/HUMBLE․.flac"
    assert off["paths"]["album_path"] == "Kendrick Lamar/DAMN/HUMBLE.flac"
    # outside the preview the importer reads the saved config again
    assert import_paths.unicode_substitution_enabled() is False
    assert import_paths._get_config_manager().get("file_organization.disc_label") == "Disc"


def test_artist_letter_reads_the_previewed_toggle(saved):
    with preview_settings({"file_organization.artistletter_symbol_fallback": True}):
        assert import_paths.artist_letter("365 Days") == "#"
    assert import_paths.artist_letter("365 Days") == "3"


def test_same_name_scenario_shows_the_clash_note_and_unique_artists_stay_plain(saved):
    template = {"album_path": "$albumartist ($albumartistclash)/$album/$track - $title"}
    assert preview_paths(template, {}, "samename")["paths"]["album_path"] == \
        "Nirvana (US grunge band)/Bleach/01 - Blew.flac"
    assert preview_paths(template, {}, "standard")["paths"]["album_path"] == \
        "Daft Punk/Random Access Memories/08 - Get Lucky.flac"


def test_compilation_files_as_various_artists_with_the_track_artist(saved):
    out = preview_paths({"compilation_path": "Compilations/$album/$track - $artist - $title"}, {})
    assert out["paths"]["compilation_path"] == (
        "Compilations/Pulp Fiction (Music From the Motion Picture)/"
        "02 - Dick Dale & His Del-Tones - Misirlou.flac")


def test_auto_disambiguation_toggle_reaches_the_folder(saved):
    template = {"album_path": "$albumartist/$album/$track - $title"}
    assert "(2009 remaster)" in preview_paths(
        template, {"auto_disambiguation": True}, "multidisc")["paths"]["album_path"]
    assert "(2009 remaster)" not in preview_paths(
        template, {"auto_disambiguation": False}, "multidisc")["paths"]["album_path"]


def test_default_single_template_also_shows_the_album_route(saved):
    default = preview_paths({"album_path": "$albumartist/${albumtype}s/$album/$title",
                             "single_path": ""}, {})
    assert default["paths"]["single_as_album"] == "Daft Punk/Singles/One More Time/One More Time.flac"
    custom = preview_paths({"single_path": "$artist/Singles/$title"}, {})
    assert custom["paths"]["single_as_album"] is None
    assert custom["paths"]["single_path"] == "Daft Punk/Singles/One More Time.flac"


def test_video_sample_and_playlist_use_their_own_renderers(saved):
    out = preview_paths({"video_path": "$artistletter/$artist/$title ($year)",
                         "sample_path": "$artist/$stem/$chop",
                         "playlist_item": "$position - $artist - $title"}, {})
    assert out["paths"]["video_path"] == "D/Daft Punk/Get Lucky (2013).mp4"
    assert out["paths"]["sample_path"] == "Daft Punk/drums/Intro chop.wav"
    assert out["paths"]["playlist_item"] == "03 - Daft Punk - Get Lucky.flac"
    # empty keeps the library filename
    assert preview_paths({"playlist_item": ""}, {})["paths"]["playlist_item"] == "08 - Get Lucky.flac"


def test_one_failing_template_leaves_the_others(saved, monkeypatch):
    def boom(*_a, **_kw):
        raise ValueError("bad video template")
    import core.downloads.music_video as music_video
    monkeypatch.setattr(music_video, "render_path", boom)
    out = preview_paths({}, {})
    assert out["paths"]["video_path"] == {"error": "bad video template"}
    assert out["paths"]["album_path"].endswith("08 - Get Lucky.flac")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_renders_every_field(saved, scenario):
    out = preview_paths({}, {}, scenario)
    assert out["scenario"] == scenario
    for key in ("album_path", "single_path", "compilation_path", "playlist_item",
                "video_path", "sample_path", "disc_label"):
        assert isinstance(out["paths"][key], str) and out["paths"][key], key
        assert "$" not in out["paths"][key], key


def test_unknown_scenario_falls_back_to_standard(saved):
    assert preview_paths({}, {}, "nope")["scenario"] == "standard"


@pytest.mark.parametrize("template,total,disc,expected", [
    ("$album/$title", 2, 2, "Disc 2"),
    ("$album/$title", 1, 1, ""),
    ("$album/$disc-$title", 2, 2, ""),
    ("$album/${discnum} $title", 2, 1, ""),
    ("$album/$cdnum $title", 3, 3, ""),
])
def test_auto_disc_folder(template, total, disc, expected):
    assert auto_disc_folder(template, total, disc, "Disc") == expected


def test_preview_module_never_imports_the_database():
    assert "music_database" not in open(path_preview.__file__, encoding="utf-8").read()
