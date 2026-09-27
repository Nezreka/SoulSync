"""#1306 (zikasak): non-latin titles matched anything.

five matchers normalized with ``[^a-z0-9 ]``. a japanese or hebrew title
folded to "" and two empty strings compare as 1.0, so track number repair
matched 君のいない夜を越えて to על הסף at 100% and renumbered the file; and
the filename "01-01 - x" (disc-track) was read as track "01", becoming
"10-01 - x".
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from core.repair_jobs import track_number_repair as tnr
from core.text.fold import fold_title, title_similarity

JA = "君のいない夜を越えて (オルゴールVer)"
HE = "על הסף"


def test_fold_keeps_every_script_and_latin_behaves_as_before():
    assert fold_title(JA) == "君のいない夜を越えて"
    assert fold_title(HE) == "על הסף"
    assert fold_title("Beyoncé - Halo (Remastered)") == "beyonce halo"
    assert title_similarity(JA, HE) == 0.0
    assert title_similarity("", "") == 0.0          # nothing is not a match
    assert title_similarity("君のいない夜を越えて", JA) == 1.0


def test_track_repair_matches_the_right_non_latin_track():
    api = [{"name": HE, "track_number": 10}, {"name": "君のいない夜を越えて", "track_number": 1}]
    track, score = tnr._match_title_to_api_track(JA, api, 0.8)
    assert track["track_number"] == 1 and score == 1.0
    track, score = tnr._match_title_to_api_track(JA, [{"name": HE, "track_number": 10}], 0.8)
    assert track is None


def _opus(path, title, tracknumber):
    """a minimal valid ogg opus file with vorbis comments."""
    from mutagen.ogg import OggPage
    from mutagen.oggopus import OggOpus
    head = b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    tags = b"OpusTags" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0)
    pages = []
    for i, (pkt, pos) in enumerate(((head, 0), (tags, 0), (b"\xfc\xff\xfe", 960))):
        p = OggPage()
        p.serial, p.sequence, p.packets, p.position = 1, i, [pkt], pos
        p.first, p.last = i == 0, i == 2
        pages.append(p.write())
    path.write_bytes(b"".join(pages))
    a = OggOpus(str(path))
    a["title"] = [title]
    a["tracknumber"] = [str(tracknumber)]
    a.save()


def test_disc_track_filename_keeps_its_shape(tmp_path):
    name = "01-01 - " + JA + ".flac"
    f = tmp_path / "x.opus"
    _opus(f, "君のいない夜を越えて", 1)
    api = [{"name": "Other", "track_number": 1}, {"name": "君のいない夜を越えて", "track_number": 7}]
    plan = tnr._plan_track_repair(str(f), name, api, 0.8)
    assert plan["correct_num"] == 7
    assert plan["new_basename"] == "01-07 - " + JA            # not "07-01 - ..."


def test_plain_prefixes_still_work(tmp_path):
    f = tmp_path / "y.opus"
    _opus(f, "Halo", 1)
    api = [{"name": "Halo", "track_number": 4}]
    plan = tnr._plan_track_repair(str(f), "01 - Halo.flac", api, 0.8)
    assert plan["new_basename"] == "04 - Halo"


def test_provider_name_match_needs_real_overlap():
    from core.metadata.source import _names_match
    assert _names_match("君の名は", "君の名は")
    assert not _names_match("君の名は", "夜に駆ける")
    assert not _names_match("君の名は", HE)


def test_bandcamp_picks_nothing_for_unrelated_non_latin_candidates():
    from core.bandcamp_client import _best_match
    cand = SimpleNamespace(name="夜に駆ける", artists=["YOASOBI"])
    assert _best_match([cand], "MY FIRST STORY", "君のいない夜を越えて") is None
    right = SimpleNamespace(name="君のいない夜を越えて", artists=["MY FIRST STORY"])
    assert _best_match([cand, right], "MY FIRST STORY", "君のいない夜を越えて") is right


def test_single_dedup_does_not_pair_different_non_latin_titles():
    from core.repair_jobs.single_album_dedup import _folded_similarity, _normalize
    assert _normalize(JA) == "君のいない夜を越えて"
    assert _folded_similarity(_normalize("夜に駆ける"), _normalize(JA)) < 0.5


def test_video_title_gate_no_longer_waves_through_everything_for_non_latin_titles():
    from core.video.release_parse import normalize_title, titles_match
    assert normalize_title("君の名は。") == "君の名は"
    assert normalize_title("The Dark Knight") == "dark knight"        # latin unchanged
    assert not titles_match("Some.Other.Movie.2016.1080p.BluRay-GRP", "君の名は。")
    assert titles_match("Some.Other.Movie.2016.1080p.BluRay-GRP", ["君の名は。", "Some Other Movie"])


@pytest.mark.parametrize("pre,expected", [("01-01", True), ("1.07", True), ("02. Title", False), ("1999-2001", False)])
def test_disc_track_prefix_pattern(pre, expected):
    assert bool(tnr._DISC_TRACK_PREFIX.match(pre)) is expected
