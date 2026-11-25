"""A Cyrillic title in a CJK folder path must survive normalize_string (#2246).

A path with any CJK character skipped transliteration entirely, and the
alphanumeric strip then deleted the Cyrillic title, so the title gate
rejected exact files such as ``.../相變臨界 (Original Soundtrack)/0101 Дом.flac``.
"""

from __future__ import annotations

from core.download_plugins.types import TrackResult
from core.matching_engine import MusicMatchingEngine
from core.spotify_client import Track

PATH = "music/塞壬唱片-MSR/相變臨界 (Original Soundtrack) (2026)/0101 Дом.flac"


def _result(filename: str) -> TrackResult:
    return TrackResult(
        username="peer",
        filename=filename,
        size=1,
        bitrate=900,
        duration=200,
        quality="flac",
        free_upload_slots=1,
        upload_speed=1,
        queue_length=0,
    )


def test_title_in_another_script_survives_next_to_cjk():
    engine = MusicMatchingEngine()
    assert "dom" in engine.normalize_string(PATH).split()


def test_cjk_characters_are_still_kept():
    engine = MusicMatchingEngine()
    assert engine.normalize_string("命の灯火") == "命の灯火"
    assert "塞壬唱片" in engine.normalize_string(PATH)


def test_exact_file_in_a_cjk_folder_passes_the_title_gate():
    engine = MusicMatchingEngine()
    track = Track(
        id="1",
        name="Дом",
        artists=["塞壬唱片-MSR", "Robert Wolf"],
        album="相變臨界 (Original Soundtrack)",
        duration_ms=200000,
        popularity=0,
    )
    assert engine.calculate_slskd_match_confidence(track, _result(PATH)) > 0.0
