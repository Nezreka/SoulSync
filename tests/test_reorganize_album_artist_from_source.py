"""Reorganize wrote the album artist it already had (LettuceSnob, discord).

Full Reorganize re-tagged artist, album and the MusicBrainz artist id from the
metadata source, but album_artist came out unchanged on every run, on M4A
(aART) and MP3 (TPE2) alike. The tag writer was fine. Reorganize handed it
SoulSync's own library artist name as the album artist, and on Navidrome that
name IS the file's old album artist tag, so a wrong variation got written
straight back. Navidrome groups artists by album artist, so the variations
never went away.

Album artist now comes from the source album like every other field. The
library name is only the fallback, and a case-only difference keeps the
user's casing (same rule reorganize uses for titles).

These run Reorganize's real context builder into the real metadata extractor
the tag writer reads from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import core.library_reorganize as lr
from core.metadata import source as src

TRACK = {"name": "Karma Police", "track_number": 6, "disc_number": 1,
         "artists": [{"name": "Radiohead"}]}


def _cfg():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "metadata_enhancement.enabled": True,
        "metadata_enhancement.tags.write_multi_artist": False,
        "metadata_enhancement.tags.feat_in_title": False,
        "metadata_enhancement.tags.artist_separator": ", ",
        "file_organization.collab_artist_mode": "first",
    }.get(key, default)
    return cfg


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)
    monkeypatch.setattr(lr, "_feat_in_title_enabled", lambda: False)


def _album_artist_tag(api_album, library_artist, track=TRACK, record_type=None):
    """what reorganize would write as the album artist tag."""
    context = lr._build_post_process_context(
        api_album, track, library_artist, "OK Computer", 1,
        record_type=record_type, album_artist=library_artist,
    )
    album_info = {"is_album": True, "album_name": "OK Computer",
                  "track_number": track["track_number"], "disc_number": 1}
    # the extractor normalizes the context in place, look before it does
    seen = {"album_artists": list(context["spotify_album"]["artists"]),
            "search_artist": context["original_search_result"]["artist"]}
    with patch.object(src, "get_config_manager", return_value=_cfg()):
        md = src.extract_source_metadata(context, context["spotify_artist"], album_info)
    return md["album_artist"], seen


def test_the_source_album_artist_replaces_the_old_variation():
    """the file said 'Radiohead [UK]', soulsync's library inherited it, and the
    source says Radiohead. reorganize must write Radiohead."""
    tag, context = _album_artist_tag({"id": "a1", "name": "OK Computer",
                                      "artists": [{"name": "Radiohead"}]}, "Radiohead [UK]")
    assert tag == "Radiohead"
    # the path builder reads the same album artist, so the folder agrees
    assert context["album_artists"] == [{"name": "Radiohead"}]


@pytest.mark.parametrize("api_album", [
    {"id": "a1", "name": "OK Computer", "artist": "Radiohead"},
    {"id": "a1", "name": "OK Computer", "artist": {"name": "Radiohead"}},
    {"id": "a1", "name": "OK Computer", "artist_name": "Radiohead"},
    # tag-mode reorganize: the album comes from the file's own tags
    {"id": "", "name": "OK Computer", "album_artist": "Radiohead"},
])
def test_every_source_shape_is_read(api_album):
    assert _album_artist_tag(api_album, "Radiohead [UK]")[0] == "Radiohead"


def test_a_case_only_difference_keeps_the_users_casing():
    tag, _ = _album_artist_tag({"id": "a1", "name": "OK Computer",
                                "artists": [{"name": "RADIOHEAD"}]}, "Radiohead")
    assert tag == "Radiohead"


@pytest.mark.parametrize("api_album", [
    {"id": "a1", "name": "OK Computer"},
    {"id": "a1", "name": "OK Computer", "artists": []},
    {"id": "a1", "name": "OK Computer", "artists": [{"name": "Unknown Artist"}]},
])
def test_no_source_artist_falls_back_to_the_library_name(api_album):
    assert _album_artist_tag(api_album, "Radiohead")[0] == "Radiohead"


def test_a_compilation_takes_the_sources_various_artists():
    track = {"name": "Song", "track_number": 1, "disc_number": 1,
             "artists": [{"name": "Some Band"}]}
    tag, context = _album_artist_tag(
        {"id": "c1", "name": "Now 50", "album_type": "compilation",
         "artists": [{"name": "Various Artists"}]},
        "Some Band", track=track, record_type="compilation",
    )
    assert tag == "Various Artists"
    # the track artist is still the track's own
    assert context["search_artist"] == "Some Band"
