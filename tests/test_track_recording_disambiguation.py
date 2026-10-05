"""Recording disambiguation plumbing for tracks (#1536).

MusicBrainz recordings carry a `disambiguation` field ("acoustic", "live",
"demo") that tells same-titled versions apart. It was dropped everywhere:
the Track dataclass had no field for it, the flat recording payload didn't
include it, the import normalization stripped it, and the filename/tag
layers never saw it. These tests pin every layer of the plumbing.
"""

from __future__ import annotations

import dataclasses

import pytest

from core.imports.album import build_album_import_context
from core.imports.paths import (
    _auto_disambiguation_enabled,
    _replace_template_variables,
    get_file_path_from_template,
    with_track_disambiguation,
)
from core.imports.staging import _normalize_track_result
from core.metadata.source import ID3_TAG_MAP, MP4_TAG_MAP
from core.musicbrainz_search import MusicBrainzSearchClient, Track


# --- search layer -----------------------------------------------------------

def _recording(**overrides):
    rec = {
        "id": "b62c3bf7-8965-451d-bb8b-2a2187e0bbc0",
        "title": "Dear Maria, Count Me In",
        "length": 180000,
        "score": 100,
        "artist-credit": [{"name": "All Time Low"}],
        "releases": [],
    }
    rec.update(overrides)
    return rec


def test_recording_to_track_reads_disambiguation():
    client = MusicBrainzSearchClient()
    track = client._recording_to_track(
        _recording(disambiguation="Connect Sets acoustic"), "All Time Low")
    assert track is not None
    assert track.disambiguation == "Connect Sets acoustic"


def test_recording_to_track_none_when_absent():
    client = MusicBrainzSearchClient()
    track = client._recording_to_track(_recording(), "All Time Low")
    assert track is not None
    assert track.disambiguation is None


def test_recording_to_track_strips_whitespace():
    client = MusicBrainzSearchClient()
    track = client._recording_to_track(_recording(disambiguation="  live  "), "x")
    assert track.disambiguation == "live"


def test_track_dataclass_field_exists():
    assert "disambiguation" in {f.name for f in dataclasses.fields(Track)}


# --- normalization / import context -----------------------------------------

def test_normalize_track_result_passes_through_disambiguation():
    client = MusicBrainzSearchClient()
    track = client._recording_to_track(
        _recording(disambiguation="acoustic"), "All Time Low")
    out = _normalize_track_result(track, "musicbrainz")
    assert out["disambiguation"] == "acoustic"


def test_normalize_track_result_empty_when_missing():
    out = _normalize_track_result({"id": "x", "name": "y"}, "spotify")
    assert out["disambiguation"] == ""


def test_build_album_import_context_carries_disambiguation():
    ctx = build_album_import_context(
        {"name": "Album", "artists": [{"name": "Artist"}]},
        {"id": "t1", "name": "Song", "disambiguation": "demo"},
        source="musicbrainz",
    )
    assert ctx["track_info"]["disambiguation"] == "demo"


def test_build_album_import_context_empty_when_missing():
    ctx = build_album_import_context(
        {"name": "Album"}, {"id": "t1", "name": "Song"}, source="spotify")
    assert ctx["track_info"]["disambiguation"] == ""


# --- template variable --------------------------------------------------------

def _ctx(**overrides):
    base = {
        "artist": "All Time Low",
        "albumartist": "All Time Low",
        "album": "Album",
        "title": "Dear Maria, Count Me In",
        "track": "01",
        "track_disambiguation": "acoustic",
        "disambiguation": "",
    }
    base.update(overrides)
    return base


def test_track_disambiguation_template_var():
    out = _replace_template_variables("$title ($track_disambiguation)", _ctx())
    assert out == "Dear Maria, Count Me In (acoustic)"


def test_track_disambiguation_bracket_form():
    out = _replace_template_variables("$title ${track_disambiguation}", _ctx())
    assert out == "Dear Maria, Count Me In acoustic"


def test_track_disambiguation_not_clobbered_by_track():
    # $track_disambiguation starts with $track; longest-prefix-first must win.
    out = _replace_template_variables("$track - $track_disambiguation", _ctx())
    assert out == "01 - acoustic"


def test_track_disambiguation_empty_renders_nothing():
    out = _replace_template_variables("$title $track_disambiguation", _ctx(track_disambiguation=""))
    assert out == "Dear Maria, Count Me In"


def test_album_disambiguation_var_untouched():
    out = _replace_template_variables("$album $disambiguation", _ctx(disambiguation="deluxe"))
    assert out == "Album deluxe"


def test_web_server_template_engine_knows_track_disambiguation():
    # web_server carries a hand-maintained copy of the template engine; a new
    # variable has to be added there too. (web_server needs mutagen, so this
    # only runs where the full deps are installed — same as the existing
    # test_web_server_template_engine_knows_the_variable.)
    mutagen = pytest.importorskip("mutagen")
    import web_server
    ctx = {"artist": "a", "albumartist": "a", "album": "b", "title": "t",
           "track_number": 1, "disc_number": 1,
           "track_disambiguation": "acoustic"}
    out = web_server._apply_path_template("$track - $title ($track_disambiguation)", ctx)
    assert out == "01 - t (acoustic)"
    out = web_server._apply_path_template("$track - $track_disambiguation", ctx)
    assert out == "01 - acoustic"


# --- auto-append ---------------------------------------------------------------

def test_with_track_disambiguation_appends_after_title():
    out = with_track_disambiguation(
        "$artist/$track - $title", "acoustic", "Dear Maria, Count Me In")
    assert out == "$artist/$track - $title ($track_disambiguation)"


def test_with_track_disambiguation_filename_only_template():
    out = with_track_disambiguation("$track - $title", "live", "Song")
    assert out == "$track - $title ($track_disambiguation)"


def test_with_track_disambiguation_skips_when_template_has_var():
    tpl = "$track - $title [$track_disambiguation]"
    assert with_track_disambiguation(tpl, "acoustic", "Song") == tpl


def test_with_track_disambiguation_skips_when_title_carries_it():
    tpl = "$track - $title"
    assert with_track_disambiguation(tpl, "live", "Song (Live)") == tpl


def test_with_track_disambiguation_word_boundary():
    # "live" must not match inside "alive".
    tpl = "$track - $title"
    assert with_track_disambiguation(tpl, "live", "Stay Alive") == \
        "$track - $title ($track_disambiguation)"


def test_with_track_disambiguation_empty_is_noop():
    tpl = "$track - $title"
    assert with_track_disambiguation(tpl, "", "Song") == tpl
    assert with_track_disambiguation(tpl, "   ", "Song") == tpl


def test_with_track_disambiguation_no_title_token_is_noop():
    tpl = "$artist/$tracknum"
    assert with_track_disambiguation(tpl, "acoustic", "Song") == tpl


def test_get_file_path_auto_appends_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "core.imports.paths._auto_disambiguation_enabled", lambda: True)
    monkeypatch.setattr(
        "core.imports.paths._get_config_manager",
        lambda: _FakeConfig({"file_organization.enabled": True,
                             "file_organization.templates": {}}),
    )
    folder, filename = get_file_path_from_template(_ctx(), "album_path")
    # Pinned placement: directly after the title, not dangling at the end.
    assert filename == "01 - Dear Maria, Count Me In (acoustic)"


def test_get_file_path_no_append_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "core.imports.paths._auto_disambiguation_enabled", lambda: False)
    monkeypatch.setattr(
        "core.imports.paths._get_config_manager",
        lambda: _FakeConfig({"file_organization.enabled": True,
                             "file_organization.templates": {}}),
    )
    folder, filename = get_file_path_from_template(_ctx(), "album_path")
    assert "acoustic" not in filename


class _FakeConfig:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- tag mapping ---------------------------------------------------------------

# --- single/album import hops (reviewer 2 blockers) -------------------------------

def test_build_single_import_context_payload_carries_disambiguation():
    from core.imports.resolution import _build_single_import_context_payload
    payload = _build_single_import_context_payload(
        {"id": "t1", "name": "Song", "disambiguation": "Connect Sets acoustic",
         "artists": [{"name": "Artist"}]},
        "musicbrainz", ["musicbrainz"],
    )
    assert payload["context"]["track_info"]["disambiguation"] == "Connect Sets acoustic"


def test_build_single_import_context_payload_empty_when_missing():
    from core.imports.resolution import _build_single_import_context_payload
    payload = _build_single_import_context_payload(
        {"id": "t1", "name": "Song", "artists": [{"name": "Artist"}]},
        "spotify", ["spotify"],
    )
    assert payload["context"]["track_info"]["disambiguation"] == ""


def test_normalize_match_track_carries_disambiguation():
    from core.imports.album import _normalize_match_track
    out = _normalize_match_track(
        {"id": "t1", "name": "Song", "disambiguation": "live"},
        "musicbrainz", {"name": "Album"},
    )
    assert out["disambiguation"] == "live"


def test_normalize_match_track_empty_when_missing():
    from core.imports.album import _normalize_match_track
    out = _normalize_match_track(
        {"id": "t1", "name": "Song"}, "spotify", {"name": "Album"})
    assert out["disambiguation"] == ""


def test_get_track_details_includes_disambiguation():
    client = MusicBrainzSearchClient()
    client._client = _FakeMBClient({
        "id": "mbid-1", "title": "Song", "length": 1000,
        "disambiguation": "demo",
        "artist-credit": [{"name": "Artist"}],
        "releases": [],
    })
    details = client.get_track_details("mbid-1")
    assert details is not None
    assert details["disambiguation"] == "demo"


def test_get_track_details_empty_when_missing():
    client = MusicBrainzSearchClient()
    client._client = _FakeMBClient({
        "id": "mbid-1", "title": "Song", "length": 1000,
        "artist-credit": [{"name": "Artist"}],
        "releases": [],
    })
    details = client.get_track_details("mbid-1")
    assert details is not None
    assert details["disambiguation"] == ""


class _FakeMBClient:
    def __init__(self, recording):
        self._recording = recording

    def get_recording(self, mbid, includes=None):
        return self._recording


# --- tag layer -----------------------------------------------------------------

def test_write_tag_dispatches_trackcomment_id3():
    from core.metadata.musicbrainz_tags import write_tag

    class FakeTXXX:
        def __init__(self, encoding=None, desc=None, text=None):
            self.desc = desc
            self.text = text

    class FakeID3(dict):
        def add(self, frame):
            self[frame.desc] = frame.text

    class FakeSymbols:
        ID3 = FakeID3
        MP4 = type("MP4", (), {})
        TXXX = FakeTXXX

    audio = _FakeAudio(FakeID3())
    write_tag(audio, "MUSICBRAINZ_TRACKCOMMENT", "acoustic", FakeSymbols())
    assert audio.tags["MusicBrainz Track Comment"] == ["acoustic"]


def test_write_tag_dispatches_trackcomment_mp4():
    from core.metadata.musicbrainz_tags import write_tag

    class FakeFreeForm:
        def __init__(self, data):
            self.data = data

    class FakeMP4(dict):
        # write_tag reads audio.tags before the isinstance(audio, MP4) branch.
        tags = None

    class FakeSymbols:
        ID3 = type("ID3", (), {})
        MP4 = FakeMP4
        MP4FreeForm = FakeFreeForm

    audio = FakeMP4()
    write_tag(audio, "MUSICBRAINZ_TRACKCOMMENT", "live", FakeSymbols())
    key = "----:com.apple.iTunes:MusicBrainz Track Comment"
    assert key in audio
    assert audio[key][0].data == b"live"


class _FakeAudio:
    def __init__(self, tags):
        self.tags = tags

    def __setitem__(self, key, value):
        self.tags[key] = value

    def __contains__(self, key):
        return key in self.tags

    def __getitem__(self, key):
        return self.tags[key]


def test_trackcomment_tag_mappings_exist():
    assert ID3_TAG_MAP["MUSICBRAINZ_TRACKCOMMENT"] == ("TXXX", "MusicBrainz Track Comment")
    assert MP4_TAG_MAP["MUSICBRAINZ_TRACKCOMMENT"] == "MusicBrainz Track Comment"


def test_trackcomment_has_settings_toggle():
    from core.metadata.source import SOURCE_TAG_CONFIG
    assert SOURCE_TAG_CONFIG["MUSICBRAINZ_TRACKCOMMENT"] == "musicbrainz.tags.recording_comment"
