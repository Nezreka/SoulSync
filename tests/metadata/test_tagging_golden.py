"""every tag the download tagger writes, pinned.

the tagger is the one piece of code every download passes through, and it is
being reshaped (one write per file, one table of where a field lives in each
container). this runs the REAL enhance_file_metadata and the REAL enrichment
writer on real files in all five containers, with only the network stood in
for, and compares every tag left in the file with tests/fixtures/
tagging_golden.json.

a difference is either a regression or a decision. if it is a decision,
regenerate with SOULSYNC_REGEN_TAGGING_GOLDEN=1 and read the diff of the
golden file in review: it is the list of what changed in people's files.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mutagen")
import mutagen  # noqa: E402

import core.metadata.enrichment as enrichment  # noqa: E402
from core.metadata import source as ms  # noqa: E402
from core.metadata.common import get_mutagen_symbols  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIXTURES / "tagging_golden.json"
FORMATS = ("flac", "mp3", "m4a", "ogg", "opus")
SYMBOLS = get_mutagen_symbols()


class Cfg:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        if key == "metadata_enhancement.embed_album_art" and key not in self.values:
            return False
        return self.values.get(key, default)


def _pre_tag(path):
    """what a file off the network arrives with: an uploader's junk, and the
    facts about the recording nobody else will supply"""
    audio = mutagen.File(path)
    if audio.tags is None:
        audio.add_tags()
    if isinstance(audio.tags, SYMBOLS.ID3):
        from mutagen.id3 import COMM, TBPM, TCOM, TIT2, TKEY, TPE1, TXXX, WXXX
        for frame in (TIT2(encoding=3, text=["yellow (www.rippers.example)"]), TPE1(encoding=3, text=["COLDPLAY"]),
                      COMM(encoding=3, lang="eng", desc="", text=["ripped by xX"]),
                      WXXX(encoding=3, desc="", url="http://rippers.example"),
                      TCOM(encoding=3, text=["Guy Berryman", "Chris Martin"]), TBPM(encoding=3, text=["173"]),
                      TKEY(encoding=3, text=["B"]),
                      TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-7.10 dB"]),
                      TXXX(encoding=3, desc="MusicBrainz Album Id", text=["stale-release-id"]),
                      TXXX(encoding=3, desc="RIPPER", text=["xX"])):
            audio.tags.add(frame)
    elif isinstance(audio, SYMBOLS.MP4):
        ff = lambda s: [SYMBOLS.MP4FreeForm(s.encode("utf-8"))]  # noqa: E731
        audio["\xa9nam"] = ["yellow (www.rippers.example)"]
        audio["\xa9ART"] = ["COLDPLAY"]
        audio["\xa9cmt"] = ["ripped by xX"]
        audio["\xa9wrt"] = ["Guy Berryman", "Chris Martin"]
        audio["tmpo"] = [173]
        audio["pgap"] = True
        audio["----:com.apple.iTunes:iTunSMPB"] = ff(" 00000000 00000840 000001C0 000000000000AC44")
        audio["----:com.apple.iTunes:replaygain_track_gain"] = ff("-7.10 dB")
        audio["----:com.apple.iTunes:MusicBrainz Album Id"] = ff("stale-release-id")
        audio["----:com.apple.iTunes:RIPPER"] = ff("xX")
    else:
        audio["title"] = ["yellow (www.rippers.example)"]
        audio["artist"] = ["COLDPLAY"]
        audio["comment"] = ["ripped by xX"]
        audio["composer"] = ["Guy Berryman", "Chris Martin"]
        audio["bpm"] = ["173"]
        audio["initialkey"] = ["B"]
        audio["replaygain_track_gain"] = ["-7.10 dB"]
        audio["musicbrainz_albumid"] = ["stale-release-id"]
        audio["ripper"] = ["xX"]
    audio.save()


def _dump(path):
    """every tag in the file, in a form that compares and reads"""
    audio = mutagen.File(path)
    out = {}
    if audio.tags is None:
        return out

    def plain(v):
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        if isinstance(v, (tuple, list)):
            return [plain(x) for x in v]
        if isinstance(v, (bool, int, float)) or v is None:
            return v
        return str(v)

    if isinstance(audio.tags, SYMBOLS.ID3):
        for key in sorted(audio.tags.keys()):
            frame = audio.tags[key]
            if hasattr(frame, "text"):
                out[key] = [str(t) for t in frame.text]
            elif hasattr(frame, "url"):
                out[key] = str(frame.url)
            elif hasattr(frame, "data"):
                out[key] = plain(frame.data)
            else:
                out[key] = repr(frame)
    else:
        for key in sorted(audio.tags.keys()):
            out[key] = plain(list(audio.tags[key]) if isinstance(audio.tags[key], (list, tuple)) else audio.tags[key])
    return out


# the network, stood in for: what a MusicBrainz + Deezer + Last.fm + Qobuz
# pass finds for this track. the album artist is corrected, as #2259 does.
def _fake_source(source_name, pp, metadata, cfg, runtime, track_title, artist_name, **kw):
    if source_name != "musicbrainz":
        return
    pp["recording_mbid"] = "11111111-1111-4111-8111-111111111111"
    pp["artist_mbid"] = "22222222-2222-4222-8222-222222222222"
    pp["release_mbid"] = "33333333-3333-4333-8333-333333333333"
    pp["id_tags"].update({
        "MUSICBRAINZ_RECORDING_ID": pp["recording_mbid"], "MUSICBRAINZ_ARTIST_ID": pp["artist_mbid"],
        "MUSICBRAINZ_RELEASE_ID": pp["release_mbid"], "DEEZER_TRACK_ID": "3135556",
        "DATE": "2000-07-10", "ARTISTSORT": "Coldplay", "ALBUMARTISTSORT": "Coldplay",
        "LABEL": "Parlophone", "BARCODE": "724352778324",
    })
    pp["mb_genres"] = ["alternative rock", "britpop"]
    pp["isrc"] = "GBAYE0000351"
    pp["mb_isrcs"] = ["GBAYE0000351"]
    pp["release_year"] = "2000"
    pp["deezer_bpm"] = 87
    pp["lastfm_tags"] = ["indie", "EDM"]
    pp["lastfm_url"] = "https://www.last.fm/music/Coldplay/_/Yellow"
    pp["genius_url"] = "https://genius.com/Coldplay-yellow-lyrics"
    pp["qobuz_copyright"] = "(P) 2000 Parlophone Records Ltd"
    pp["qobuz_label"] = "Parlophone UK"
    pp["audiodb_mood"] = "Happy"
    pp["audiodb_style"] = "Rock/Pop"
    if metadata.get("_golden_corrected_album_artist"):
        metadata["album_artist"] = metadata["_golden_corrected_album_artist"]
        metadata["_album_artists_list"] = [metadata["_golden_corrected_album_artist"]]


SCENARIOS = {
    "plain": ({}, {"title": "Yellow", "artist": "Coldplay", "album_artist": "Coldplay", "album": "Parachutes",
                   "date": "2000", "genre": "rock", "track_number": 5, "total_tracks": 10, "disc_number": 1,
                   "total_discs": 1, "_artists_list": ["Coldplay"], "_album_artists_list": ["Coldplay"]}),
    "guests": ({"metadata_enhancement.tags.write_multi_artist": True,
                "metadata_enhancement.tags.feat_in_title": True},
               {"title": "Princess of China (feat. Rihanna)", "artist": "Coldplay", "album_artist": "Coldplay",
                "album": "Mylo Xyloto", "date": "2011-10-24", "genre": "pop, R&B", "track_number": 10,
                "total_tracks": 14, "disc_number": 1, "_artists_list": ["Coldplay", "Rihanna"],
                "_album_artists_list": ["Coldplay"]}),
    "compilation": ({"metadata_enhancement.tags.write_multi_artist": True},
                    {"title": "Yellow", "artist": "Coldplay, Guest", "album_artist": "Coldplay",
                     "album": "Now 46", "date": "2000", "track_number": 0, "total_tracks": 40, "disc_number": 2,
                     "total_discs": 2, "_artists_list": ["Coldplay", "Guest"], "_album_artists_list": ["Coldplay"],
                     "_golden_corrected_album_artist": "Various Artists"}),
}


def _run(tmp_path, ext, scenario):
    values, metadata = SCENARIOS[scenario]
    path = str(tmp_path / f"{scenario}.{ext}")
    shutil.copy(FIXTURES / "audio" / f"silence.{ext}", path)
    _pre_tag(path)
    cfg = Cfg(values)
    with patch.object(enrichment, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_database", return_value=MagicMock()), \
         patch.object(ms, "_process_metasync_depot_source", lambda *a, **k: None), \
         patch.object(ms, "_process_source_enrichment", _fake_source), \
         patch.object(enrichment, "strip_all_non_audio_tags", lambda *a, **k: None), \
         patch.object(enrichment, "extract_source_metadata", lambda *a, **k: dict(metadata)), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch("core.metadata.registry.is_jiosaavn_enabled", return_value=False), \
         patch("core.metadata.registry.is_source_enabled", return_value=False):
        ok = enrichment.enhance_file_metadata(
            path, context={"_audio_quality": "FLAC 16bit"}, artist={"name": "Coldplay"}, album_info={})
    assert ok is True
    return _dump(path)


def _all(tmp_path):
    return {f"{scenario}.{ext}": _run(tmp_path, ext, scenario) for scenario in SCENARIOS for ext in FORMATS}


def test_every_tag_the_tagger_writes_is_the_one_on_record(tmp_path):
    got = _all(tmp_path)
    if os.environ.get("SOULSYNC_REGEN_TAGGING_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("golden regenerated")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name in sorted(want):
        assert got.get(name) == want[name], name
    assert sorted(got) == sorted(want)


# ── one write, after the lookups ────────────────────────────────────────────

def _run_with(tmp_path, ext, **patches):
    values, metadata = SCENARIOS["plain"]
    path = str(tmp_path / f"t.{ext}")
    shutil.copy(FIXTURES / "audio" / f"silence.{ext}", path)
    cfg = Cfg(values)
    base = dict(_process_metasync_depot_source=lambda *a, **k: None, _process_source_enrichment=_fake_source)
    base.update(patches)
    with patch.object(enrichment, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_database", return_value=MagicMock()), \
         patch.multiple(ms, **base), \
         patch.object(enrichment, "strip_all_non_audio_tags", lambda *a, **k: None), \
         patch.object(enrichment, "extract_source_metadata", lambda *a, **k: dict(metadata)), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch("core.metadata.registry.is_jiosaavn_enabled", return_value=False), \
         patch("core.metadata.registry.is_source_enabled", return_value=False):
        enrichment.enhance_file_metadata(path, context={}, artist={"name": "Coldplay"}, album_info={})
    return _dump(path)


@pytest.mark.parametrize("ext", FORMATS)
def test_the_core_tags_are_written_when_the_lookups_fall_over(tmp_path, ext):
    """the core tags are written between looking and writing. a lookup that
    raises before it gets there must not leave the file without them."""
    def boom(*a, **k):
        raise RuntimeError("musicbrainz is down")
    tags = _run_with(tmp_path, ext, _collect_source_ids=boom)
    title = tags.get("TIT2") or tags.get("title") or tags.get("\xa9nam")
    assert title == ["Yellow"]


@pytest.mark.parametrize("ext", FORMATS)
def test_a_source_that_corrects_the_artist_list_is_the_list_written(tmp_path, ext):
    """the lists are read when the core tags are written, not before the
    lookups: a corrected album artist used to be patched in afterwards and
    its per-artist list was left as it had been"""
    def source(source_name, pp, metadata, *a, **k):
        if source_name == "musicbrainz":
            metadata["album_artist"] = "Coldplay & Guest"
            metadata["_album_artists_list"] = ["Coldplay", "Guest"]
    values, metadata = SCENARIOS["plain"]
    path = str(tmp_path / f"t.{ext}")
    shutil.copy(FIXTURES / "audio" / f"silence.{ext}", path)
    cfg = Cfg({"metadata_enhancement.tags.write_multi_artist": True})
    with patch.object(enrichment, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_database", return_value=MagicMock()), \
         patch.object(ms, "_process_metasync_depot_source", lambda *a, **k: None), \
         patch.object(ms, "_process_source_enrichment", source), \
         patch.object(enrichment, "strip_all_non_audio_tags", lambda *a, **k: None), \
         patch.object(enrichment, "extract_source_metadata", lambda *a, **k: dict(metadata)), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch("core.metadata.registry.is_jiosaavn_enabled", return_value=False), \
         patch("core.metadata.registry.is_source_enabled", return_value=False):
        enrichment.enhance_file_metadata(path, context={}, artist={"name": "Coldplay"}, album_info={})
    tags = _dump(path)
    assert (tags.get("TPE2") or tags.get("albumartist") or tags.get("aART")) == ["Coldplay & Guest"]
    listed = (tags.get("TXXX:Album Artists") or tags.get("albumartists")
              or tags.get("----:com.apple.iTunes:ALBUMARTISTS"))
    assert listed == ["Coldplay", "Guest"]


@pytest.mark.parametrize("fails", [True, False])
def test_the_lookup_step_hands_over_exactly_once(tmp_path, fails):
    """embed_source_ids calls on_resolved once, between looking and writing,
    and still once when the looking raised"""
    path = str(tmp_path / "t.flac")
    shutil.copy(FIXTURES / "audio" / "silence.flac", path)
    audio = mutagen.File(path)
    calls = []

    def boom(*a, **k):
        raise RuntimeError("down")
    cfg = Cfg()
    with patch.object(ms, "get_config_manager", return_value=cfg), \
         patch.object(ms, "get_database", return_value=MagicMock()), \
         patch.object(ms, "_process_metasync_depot_source", lambda *a, **k: None), \
         patch.object(ms, "_process_source_enrichment", _fake_source), \
         patch.object(ms, "_collect_source_ids", boom if fails else (lambda *a, **k: {})), \
         patch("core.metadata.registry.is_jiosaavn_enabled", return_value=False), \
         patch("core.metadata.registry.is_source_enabled", return_value=False):
        ms.embed_source_ids(audio, dict(SCENARIOS["plain"][1]), {}, on_resolved=lambda: calls.append(1))
    assert calls == [1]
