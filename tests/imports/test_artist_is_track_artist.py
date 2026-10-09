"""$artist is the track's artist, $albumartist the album's.

a label comp credited to its dj ("Capital Heaven Five Years" by Vlad Jet) but not
typed compilation named every file after the dj while the tags said the real
artist: "Vlad Jet - Blue Skies (Dan.K Remix)" by Framewerk.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as import_paths
from core.imports.paths import build_final_path_for_track


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": "$albumartist/$album/$artist - $title",
        },
        "file_organization.collab_artist_mode": "first",
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    return tmp_path


def _dest(album_artist, track_artist, album_type="album"):
    title = "Blue Skies (Dan.K Remix)"
    album = "Capital Heaven Five Years"
    context = {
        "artist": {"name": album_artist},
        "album": {"name": album, "id": "a1", "release_date": "2020-01-01",
                  "total_tracks": 20, "album_type": album_type,
                  "artists": [{"name": album_artist}]},
        "track_info": {"name": title, "id": "t1", "track_number": 3,
                       "disc_number": 1, "artists": [{"name": track_artist}]},
        "original_search_result": {"title": title, "clean_title": title,
                                   "clean_album": album, "clean_artist": track_artist,
                                   "artists": [{"name": track_artist}]},
        "source": "spotify", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": album_artist},
        {"is_album": True, "album_name": album, "track_number": 3, "disc_number": 1},
        ".mp3", create_dirs=False)
    return path


def test_artist_is_the_track_artist_when_the_album_credits_someone_else(cfg):
    path = _dest("Vlad Jet", "Framewerk")
    assert os.path.basename(path) == "Framewerk - Blue Skies (Dan.K Remix).mp3"
    # the folder still follows the album's credit
    assert os.path.basename(os.path.dirname(os.path.dirname(path))) == "Vlad Jet"


def test_same_artist_album_is_unchanged(cfg):
    path = _dest("Framewerk", "Framewerk")
    assert os.path.basename(path) == "Framewerk - Blue Skies (Dan.K Remix).mp3"


def test_compilation_still_uses_the_track_artist(cfg):
    # compilations go through compilation_path: "$track - $artist - $title"
    path = _dest("Various Artists", "Framewerk", album_type="compilation")
    assert os.path.basename(path) == "03 - Framewerk - Blue Skies (Dan.K Remix).mp3"
