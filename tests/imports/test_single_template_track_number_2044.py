"""#2044 follow-up: every track of a multi-track single was numbered 01.

Ericsson: with a customized Single Path Template ending "$track - $title", a
4-track single came out as four files numbered 01. the single branch wrote
track 1 and disc 1 whatever the release said.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as import_paths
from core.imports.paths import build_final_path_for_track

ALBUM = "$albumartist/$albumtype/$albumartist - $album ($year)/$discalways-$track - $title"
SINGLE = "$albumartist/$albumtype/$albumartist - $album ($year)/$track - $title"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _cfg(monkeypatch, tmp_path, single=SINGLE):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": ALBUM, "single_path": single, "compilation_path": ALBUM,
        },
        "file_organization.detect_compilations": False,
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(import_paths, "library_has_artist", lambda n: False)


def _file(tmp_path, number, *, disc=1, total_discs=1, album_info=True, title=None):
    title = title or f"Song {number}"
    context = {
        "artist": {"name": "Band"},
        "album": {"name": "Maxi", "id": "a1", "release_date": "2020-01-01",
                  "total_tracks": 4, "album_type": "single", "total_discs": total_discs,
                  "artists": [{"name": "Band"}]},
        "track_info": {"name": title, "id": f"t{number}", "track_number": number,
                       "disc_number": disc, "artists": [{"name": "Band"}]},
        "original_search_result": {"title": title, "clean_title": title,
                                   "clean_album": "Maxi", "artists": [{"name": "Band"}]},
        "source": "musicbrainz", "is_album_download": True,
    }
    info = ({"is_album": True, "album_name": "Maxi", "track_number": number,
             "disc_number": disc, "album_type": "single"} if album_info else None)
    path, _ = build_final_path_for_track(context, {"name": "Band"}, info, ".flac",
                                         create_dirs=False)
    return os.path.basename(path)


def test_each_track_of_a_single_keeps_its_own_number(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    assert [_file(tmp_path, n) for n in (1, 2, 3, 4)] == [
        "01 - Song 1.flac", "02 - Song 2.flac", "03 - Song 3.flac", "04 - Song 4.flac"]


def test_the_disc_is_the_release_s_disc_too(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, single="$albumartist/$album/$discalways-$track - $title")
    assert _file(tmp_path, 3, disc=2, total_discs=2) == "02-03 - Song 3.flac"
    # $disc shows on a multi-disc single even when the release never said how many discs
    _cfg(monkeypatch, tmp_path, single="$albumartist/$album/$disc-$track - $title")
    assert _file(tmp_path, 3, disc=2) == "02-03 - Song 3.flac"
    assert _file(tmp_path, 3) == "03 - Song 3.flac"


@pytest.mark.parametrize("number", [0, None, "x"])
def test_a_number_the_source_never_gave_is_one(monkeypatch, tmp_path, number):
    _cfg(monkeypatch, tmp_path)
    assert _file(tmp_path, number, title="Song") == "01 - Song.flac"


def test_a_lone_track_with_no_release_behind_it_is_still_one(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    assert _file(tmp_path, 7, album_info=False, title="Song") == "01 - Song.flac"
