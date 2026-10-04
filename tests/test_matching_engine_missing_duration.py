"""Missing catalogue runtimes must not turn download hits into search errors."""

import pytest

from core.matching_engine import MusicMatchingEngine


@pytest.mark.parametrize("first, second", [(None, 255_000), (255_000, None), (None, None)])
def test_missing_duration_has_neutral_similarity(first, second):
    assert MusicMatchingEngine().duration_similarity(first, second) == 0.5


@pytest.mark.parametrize("source_ms, candidate_ms", [(None, 255_000), (255_000, None), (None, None)])
def test_exact_recording_matches_with_missing_duration(source_ms, candidate_ms):
    confidence, match_type = MusicMatchingEngine().score_track_match(
        source_title="West Coast",
        source_artists=["Lana Del Rey"],
        source_duration_ms=source_ms,
        candidate_title="West Coast",
        candidate_artists=["Lana Del Rey"],
        candidate_duration_ms=candidate_ms,
    )

    assert confidence >= 0.9
    assert match_type == "core_title_match"


@pytest.mark.parametrize("source_ms, candidate_ms", [(None, 255_000), (255_000, None)])
def test_fuzzy_recording_matches_with_missing_duration(source_ms, candidate_ms):
    confidence, match_type = MusicMatchingEngine().score_track_match(
        source_title="West Coast",
        source_artists=["Lana Del Rey"],
        source_duration_ms=source_ms,
        candidate_title="West Coats",
        candidate_artists=["Lana Del Rey"],
        candidate_duration_ms=candidate_ms,
    )

    assert confidence >= 0.6
    assert match_type == "standard_match"


def test_known_preview_duration_still_blocks_exact_title_fast_path():
    confidence, match_type = MusicMatchingEngine().score_track_match(
        source_title="West Coast",
        source_artists=["Lana Del Rey"],
        source_duration_ms=255_000,
        candidate_title="West Coast",
        candidate_artists=["Lana Del Rey"],
        candidate_duration_ms=30_000,
    )

    assert confidence < 0.9
    assert match_type == "standard_match"
