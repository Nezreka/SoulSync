"""a release keeps its own album artist when its tracks are by others (#2133, #2135).

SeadogsBooty: Library Reorganize put a Group Therapy collection in 95 artist
folders, 422 of 426 tracks outside Above & Beyond, and downloads tagged each
track's own artist as the album artist. the #1567 poisoned-batch check read
"album by Above & Beyond, track by Kyau & Albert, no provider album id" as a
stranger's album stamped on the track, and filed and tagged it under the
track's artist. the library's own album and an album the user typed in are
not guesses: their artist is the release's.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import core.imports.paths as import_paths
from core.imports.context import (
    get_import_context_album,
    get_import_original_search,
    get_import_track_info,
    normalize_import_context,
)
from core.imports.paths import build_final_path_for_track
from core.library_reorganize import _build_post_process_context
from core.metadata import source as src_module
from core.metadata.source import evaluate_batch_album_distrust, extract_source_metadata
from core.search.manual_download import build_manual_tasks

BAND = "Above & Beyond"
TEMPLATE = "$albumartist/$album/$track - $artist - $title"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _verdict(context):
    ti = get_import_track_info(context)
    return evaluate_batch_album_distrust(
        get_import_original_search(context), ti, get_import_context_album(context),
        ti.get("_explicit_artist_context"))


def _reorganize_context(track_artist, album_type="album"):
    context = _build_post_process_context(
        {"id": "", "name": "Group Therapy 250", "release_date": "2017-09-15",
         "album_type": album_type, "total_tracks": 40},
        {"name": "Kaleidoscope", "track_number": 3, "disc_number": 1,
         "artists": [{"name": track_artist}]},
        BAND, "Group Therapy 250", 1, record_type=album_type)
    context["_no_album_folder_reuse"] = True
    return context


def _filed(monkeypatch, tmp_path, context):
    """what the import pipeline does with the verdict, then the folder."""
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {"album_path": TEMPLATE, "compilation_path": TEMPLATE},
        "file_organization.collab_artist_mode": "first",
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(import_paths, "library_has_artist", lambda n: False)
    monkeypatch.setattr(import_paths, "library_has_identified_artist", lambda n: False)
    verdict = _verdict(normalize_import_context(dict(context)))
    if verdict["distrusted"]:
        context["_batch_album_distrusted"] = True
        context["_track_own_artist_display_names"] = verdict["own_artist_display_names"]
    path, _ = build_final_path_for_track(
        context, context["spotify_artist"],
        {"is_album": True, "album_name": "Group Therapy 250", "track_number": 3,
         "disc_number": 1, "album_type": context["album_type"]},
        ".flac", create_dirs=False)
    return os.path.relpath(path, tmp_path).split(os.sep)


def _tags(context, artist):
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", lambda n: False), \
            patch.object(src_module, "library_has_identified_artist", lambda n: False):
        return extract_source_metadata(context, artist, {"album_name": "Group Therapy 250"})


def test_reorganize_keeps_a_mix_album_under_its_album_artist(monkeypatch, tmp_path):
    for album_type in ("album", "compilation"):
        context = _reorganize_context("Kyau & Albert", album_type)
        assert not _verdict(normalize_import_context(dict(context)))["distrusted"]
        parts = _filed(monkeypatch, tmp_path, _reorganize_context("Kyau & Albert", album_type))
        assert parts[:2] == [BAND, "Group Therapy 250"], album_type
        assert parts[2] == "03 - Kyau & Albert - Kaleidoscope.flac"


def test_reorganize_tags_the_release_artist_and_the_track_artist():
    context = _reorganize_context("Kyau & Albert", "compilation")
    meta = _tags(context, context["spotify_artist"])
    assert meta["album_artist"] == BAND
    assert meta["artist"] == "Kyau & Albert"


def test_a_manual_download_keeps_the_album_artist_the_user_typed():
    album_ctx, tasks = build_manual_tasks(
        [{"key": "f1", "duration": 1}],
        {"name": "Group Therapy 250", "artist": BAND},
        [{"file_key": "f1", "title": "Kaleidoscope", "track_number": 3,
          "artist": "Kyau & Albert"}],
        file_key=lambda f: f["key"])
    _file, info = tasks[0]
    context = {"track_info": info, "album": album_ctx,
               "original_search_result": {"title": "Kaleidoscope",
                                          "artists": [{"name": "Kyau & Albert"}]},
               "source": "manual"}
    meta = _tags(context, {"name": BAND})
    assert meta["album_artist"] == BAND
    assert meta["artist"] == "Kyau & Albert"


def test_a_guessed_batch_album_is_still_distrusted():
    """#1567 stays: a wishlist batch with no album id and a stranger's artist."""
    context = normalize_import_context({
        "track_info": {"name": "Song", "artists": [{"name": "Rihanna"}],
                       "_explicit_artist_context": {"name": "Eminem"}},
        "album": {"id": "wishlist_album", "name": "Recovery", "artists": [{"name": "Eminem"}]},
        "original_search_result": {"title": "Song", "artists": [{"name": "Rihanna"}]},
    })
    assert _verdict(context)["distrusted"]
