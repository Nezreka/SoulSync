"""#1860 (QT3496): $explicit / $contentrating / $contentratingfull.

explicit and clean versions of a track rendered the same filename and could
overwrite each other. the tokens badge a name [E] / [C]. a folder reads the
album's flag (a per-track value there would split one album across two
folders), the filename reads the track's, and unknown renders nothing.
"""

from __future__ import annotations

import pytest

import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.paths import apply_content_rating, build_final_path_for_track, explicit_flag


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), (1, True), (0, False),
    ("explicit", True), ("notExplicit", False), ("cleaned", False), ("true", True),
    (None, None), ("", None), ("maybe", None),
])
def test_explicit_flag_reads_every_spelling(value, expected):
    assert explicit_flag(value) is expected


def test_filename_reads_the_track_folder_reads_the_album():
    out = apply_content_rating("$album $explicit/$title $contentrating", track_explicit=False,
                               album_explicit=True)
    assert out == "$album [E]/$title [C]"


def test_unknown_renders_nothing():
    assert apply_content_rating("$title $contentrating$explicit", None, None) == "$title "


def test_full_word_and_bracket_forms():
    out = apply_content_rating("$title ${contentratingfull}x $contentratingfull", True, None)
    assert out == "$title [Explicit]x [Explicit]"


def test_filename_only_template_uses_the_track():
    assert apply_content_rating("$artist - $title $explicit", True, False) == "$artist - $title [E]"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def template(monkeypatch, tmp_path):
    def use(album_path):
        config = _Config({
            "soulseek.transfer_path": str(tmp_path / "T"),
            "file_organization.enabled": True,
            "file_organization.templates": {"album_path": album_path},
            "file_organization.collab_artist_mode": "first",
            "file_organization.auto_disambiguation": False,
        })
        monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
        monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
        return tmp_path / "T"
    return use


def _destination(track_explicit, album_explicit):
    context = {
        "artist": {"name": "Kendrick Lamar"},
        "album": {"name": "Damn", "id": "alb", "release_date": "2017-04-14", "total_tracks": 14,
                  "total_discs": 1, "total_discs_declared": True, "album_type": "album",
                  "artists": [{"name": "Kendrick Lamar"}], "explicit": album_explicit},
        "track_info": {"name": "Humble", "id": "t", "track_number": 8, "disc_number": 1,
                       "artists": [{"name": "Kendrick Lamar"}], "explicit": track_explicit},
        "original_search_result": {"title": "Humble", "clean_title": "Humble",
                                   "clean_album": "Damn", "clean_artist": "Kendrick Lamar",
                                   "artists": [{"name": "Kendrick Lamar"}]},
        "source": "deezer", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": "Kendrick Lamar"},
        {"is_album": True, "album_name": "Damn", "track_number": 8, "disc_number": 1},
        ".flac", create_dirs=False)
    return path


def test_explicit_and_clean_versions_get_different_paths(template):
    root = template("$albumartist/$album $explicit/$track - $title $contentrating")
    explicit = _destination(True, True)
    clean = _destination(False, False)
    assert explicit == str(root / "Kendrick Lamar" / "Damn [E]" / "08 - Humble [E].flac")
    assert clean == str(root / "Kendrick Lamar" / "Damn" / "08 - Humble [C].flac")


def test_unknown_flag_leaves_a_clean_name(template):
    root = template("$albumartist/$album $explicit/$track - $title $contentrating")
    assert _destination(None, None) == str(root / "Kendrick Lamar" / "Damn" / "08 - Humble.flac")


def test_reorganize_falls_back_to_the_library_flag():
    # tag mode has no source flag: the library's own column keeps the badge
    # the import gave the file, so reorganize doesn't want to strip it
    ctx = lr._build_post_process_context(
        {"name": "Damn", "id": "alb"}, {"name": "Humble", "track_number": 8},
        "Kendrick Lamar", "Damn", 1, local_explicit=1, local_album_explicit=1)
    assert explicit_flag(ctx["track_info"]["explicit"]) is True
    assert explicit_flag(ctx["spotify_album"]["explicit"]) is True


def test_reorganize_prefers_the_source_flag():
    ctx = lr._build_post_process_context(
        {"name": "Damn", "id": "alb", "explicit": False},
        {"name": "Humble", "track_number": 8, "explicit": False},
        "Kendrick Lamar", "Damn", 1, local_explicit=1, local_album_explicit=1)
    assert ctx["track_info"]["explicit"] is False
    assert ctx["spotify_album"]["explicit"] is False


def test_web_server_template_copy_never_keeps_a_literal_token(monkeypatch):
    import web_server
    out = web_server._apply_path_template(
        "$albumartist/$album $explicit/$title $contentratingfull",
        {"artist": "A", "albumartist": "A", "album": "B", "title": "C",
         "track_number": 1, "disc_number": 1, "explicit": True})
    assert "$" not in out
    assert out.endswith("C [Explicit]")
