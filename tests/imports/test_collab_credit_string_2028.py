"""'first listed artist' when the source gives the whole credit as one string (#2028).

SeadogsBooty: with collab mode on first listed, releases still landed in
"Creed, 3 Doors Down & Finger Eleven/Compilation/...", "Johannes Brahms;
Joshua Bell/..." and the like. the album's artist list held only that one
combined name, so the list rules had nothing to split.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import core.imports.paths as import_paths
from core.imports.collab_artist import credit_segments, split_credit_primary
from core.imports.paths import build_final_path_for_track
from core.metadata import source as src_module

TEMPLATE = "$albumartist/$albumtype/[$year] $album/$track - $title"
CREED = "Creed, 3 Doors Down & Finger Eleven"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _cfg(monkeypatch, tmp_path, mode="first", library=()):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": TEMPLATE, "single_path": TEMPLATE, "compilation_path": TEMPLATE,
        },
        "file_organization.collab_artist_mode": mode,
        "file_organization.detect_compilations": False,
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    known = {n.casefold() for n in library}
    monkeypatch.setattr(import_paths, "library_has_artist", lambda n: n.casefold() in known)
    return config


def _path(tmp_path, album_artist, track_artist, album_type="compilation", track_count=12):
    """a wishlist / watchlist shaped album download: the artist context is the
    track's own artist, the album names one combined credit."""
    context = {
        "artist": {"name": track_artist},
        "album": {"name": "Hits", "id": "a1", "release_date": "2004-01-01",
                  "total_tracks": track_count, "album_type": album_type,
                  "artists": [{"name": album_artist}]},
        "track_info": {"name": "Song", "id": "t1", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": track_artist}]},
        "original_search_result": {"title": "Song", "clean_title": "Song",
                                   "clean_album": "Hits", "artists": [{"name": track_artist}]},
        "source": "deezer", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": track_artist},
        {"is_album": True, "album_name": "Hits", "track_number": 1,
         "disc_number": 1, "album_type": album_type},
        ".flac", create_dirs=False)
    return os.path.relpath(path, tmp_path).split(os.sep)


def test_every_track_of_a_combined_compilation_files_under_the_first_artist(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    for singer in ("Creed", "3 Doors Down", "Finger Eleven"):
        parts = _path(tmp_path, CREED, singer)
        assert parts[:2] == ["Creed", "Compilation"], singer


def test_albums_and_singles_too(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    assert _path(tmp_path, CREED, "3 Doors Down", album_type="album")[0] == "Creed"
    assert _path(tmp_path, CREED, "3 Doors Down", album_type="single", track_count=1)[0] == "Creed"


def test_classical_credits_with_semicolons_split(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    assert _path(tmp_path, "Johannes Brahms; Joshua Bell", "Johannes Brahms; Joshua Bell")[0] \
        == "Johannes Brahms"
    long_credit = ("Пётр Ильич Чайковский, Henryk Wieniawski, Johannes Brahms, "
                   "Robert Schumann; Joshua Bell")
    assert _path(tmp_path, long_credit, long_credit)[0] == "Пётр Ильич Чайковский"
    barber = "Samuel Barber; Ruth Golden, David Zinman, Terry Edwards, Donald Barra"
    assert _path(tmp_path, barber, barber)[0] == "Samuel Barber"


def test_a_duo_named_first_keeps_its_name(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    credit = "Axwell Λ Ingrosso, Axwell & Sebastian Ingrosso"
    assert _path(tmp_path, credit, "Axwell Λ Ingrosso")[0] == "Axwell Λ Ingrosso"
    assert _path(tmp_path, credit, "Axwell & Sebastian Ingrosso")[0] == "Axwell Λ Ingrosso"


def test_the_library_vouches_when_the_track_names_nobody(monkeypatch, tmp_path):
    """the track carries the combined credit too; the library holds two of its artists."""
    _cfg(monkeypatch, tmp_path, library=("Creed", "3 Doors Down"))
    assert _path(tmp_path, CREED, CREED)[0] == "Creed"


def test_band_names_with_commas_and_ampersands_stay_whole(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, library=("Earth",))
    for band in ("Earth, Wind & Fire", "Simon & Garfunkel", "Tyler, The Creator", "Bad Company"):
        assert _path(tmp_path, band, band, album_type="album")[0] == band


def test_full_mode_keeps_the_combined_folder(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, mode="full")
    assert _path(tmp_path, CREED, "3 Doors Down")[0] == CREED


def test_split_rules():
    assert credit_segments(CREED) == ["Creed", "3 Doors Down", "Finger Eleven"]
    assert credit_segments("AC/DC") == ["AC/DC"]
    assert split_credit_primary("Artist A / Artist B") == "Artist A"
    assert split_credit_primary(CREED) == ""
    assert split_credit_primary(CREED, ["Finger Eleven"]) == "Creed"


def test_album_artist_tag_follows_the_same_rule():
    """the tag is what the media server and the library build the artist from."""
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "3 Doors Down"}]},
        "spotify_album": {"id": "133406982", "name": "Hits", "artists": [{"name": CREED}]},
        "source": "deezer",
    }
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", lambda n: False):
        meta = src_module.extract_source_metadata(
            context, {"name": "3 Doors Down"}, {"album_name": "Hits"})
    assert meta["album_artist"] == "Creed"


def test_web_server_copy_of_the_replacer_agrees(monkeypatch):
    import web_server

    monkeypatch.setattr(web_server, "config_manager",
                        _Config({"file_organization.collab_artist_mode": "first"}))
    monkeypatch.setattr(web_server, "library_has_artist", lambda n: False)
    ctx = {"artist": "3 Doors Down", "albumartist": CREED, "album": "Hits",
           "title": "Song", "track_number": 1, "disc_number": 1, "year": "2004",
           "_artists_list": [{"name": CREED}], "_track_artists_list": [{"name": "3 Doors Down"}]}
    assert web_server._apply_path_template("$albumartist/$album", ctx) == "Creed/Hits"
