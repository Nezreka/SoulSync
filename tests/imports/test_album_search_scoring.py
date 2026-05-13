"""Pin `_score_album_search_result` weight math.

Helper extracted from the inline scoring block inside
`_search_metadata_source` (auto-import album identification). Lifting
it to a pure function lets each weight be tested in isolation
without mocking the full source-chain orchestrator.

Weights (constants in `core.auto_import_worker`):
  - `_ALBUM_NAME_WEIGHT` = 0.5
  - `_ARTIST_NAME_WEIGHT` = 0.2 (skipped when target_artist falsy)
  - `_TRACK_COUNT_WEIGHT` = 0.3 (skipped when either side has 0 tracks)

Maximum score is 1.0 when all three components match perfectly. The
0.4 threshold in the orchestrator means a result needs at least one
strong signal plus a partial second — pure track-count match alone
(0.3) is below threshold.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.auto_import_worker import (
    _ALBUM_NAME_WEIGHT,
    _ARTIST_NAME_WEIGHT,
    _TRACK_COUNT_WEIGHT,
    _score_album_search_result,
)


def _result(name: str, artist_name: str = "", total_tracks: int = 0):
    """Minimal album-result stub matching the shape `search_albums`
    returns. `artists` is the list-of-dicts shape every adapter uses."""
    artists = [{"name": artist_name}] if artist_name else []
    return SimpleNamespace(name=name, artists=artists, total_tracks=total_tracks)


# ---------------------------------------------------------------------------
# Component weights — pinned at the boundary
# ---------------------------------------------------------------------------


class TestWeightConstants:
    def test_weights_sum_to_one(self):
        """Total weight budget = 1.0. If a weight is bumped without
        adjusting another, perfect-match score drifts above/below 1.0
        and the 0.4 threshold semantics shift silently."""
        total = _ALBUM_NAME_WEIGHT + _ARTIST_NAME_WEIGHT + _TRACK_COUNT_WEIGHT
        assert total == pytest.approx(1.0, abs=1e-9), (
            f"Weights must sum to 1.0; got {total}"
        )

    def test_album_weight_is_dominant(self):
        """Album name has 50% — strongest signal. If artist or track
        count weight ever exceeds album weight, the matching semantics
        flip (e.g. a wrong-album-right-count result could outscore
        a right-album-wrong-count one). Pin so a future weight tweak
        doesn't break this invariant."""
        assert _ALBUM_NAME_WEIGHT > _ARTIST_NAME_WEIGHT
        assert _ALBUM_NAME_WEIGHT > _TRACK_COUNT_WEIGHT


# ---------------------------------------------------------------------------
# Perfect match → 1.0
# ---------------------------------------------------------------------------


class TestPerfectMatch:
    def test_all_three_perfect_returns_one(self):
        r = _result("Test Album", "Test Artist", total_tracks=10)
        score = _score_album_search_result(r, "Test Album", "Test Artist", file_count=10)
        assert score == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Album name component
# ---------------------------------------------------------------------------


class TestAlbumNameWeight:
    def test_exact_album_match_no_other_signals(self):
        """Album name matches perfectly but no artist provided and
        no track count match — score is just 50%."""
        r = _result("Test Album", total_tracks=0)
        score = _score_album_search_result(r, "Test Album", target_artist=None, file_count=0)
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)

    def test_completely_different_album_zero_album_component(self):
        r = _result("Totally Different", total_tracks=0)
        score = _score_album_search_result(r, "Test Album", target_artist=None, file_count=0)
        # SequenceMatcher would return some small non-zero similarity even
        # for fully different strings, so just verify it's well below 0.5
        assert score < _ALBUM_NAME_WEIGHT * 0.5


# ---------------------------------------------------------------------------
# Artist component
# ---------------------------------------------------------------------------


class TestArtistWeight:
    def test_artist_match_adds_full_artist_weight(self):
        r = _result("Album", "Artist", total_tracks=0)
        with_artist = _score_album_search_result(r, "Album", "Artist", file_count=0)
        without_artist = _score_album_search_result(r, "Album", None, file_count=0)
        # Difference = artist weight
        assert with_artist - without_artist == pytest.approx(_ARTIST_NAME_WEIGHT, abs=1e-9)

    def test_target_artist_none_skips_artist_component(self):
        """When target_artist is falsy (None / empty), artist weight
        contributes zero — not a penalty, not a bonus. Lets album-
        only searches (e.g. from a folder name with no artist info)
        still hit the threshold via album + track count alone."""
        r = _result("Album", "WrongArtist", total_tracks=10)
        score = _score_album_search_result(r, "Album", None, file_count=10)
        # Album + track count perfect = 0.5 + 0.3 = 0.8 (artist weight skipped)
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT + _TRACK_COUNT_WEIGHT, abs=1e-9)

    def test_string_artist_not_dict_still_scored(self):
        """Some adapters return `artists` as list-of-strings instead
        of list-of-dicts. Helper must handle both shapes."""
        r = SimpleNamespace(name="Album", artists=["Just A String"], total_tracks=0)
        score_string = _score_album_search_result(r, "Album", "Just A String", 0)
        assert score_string >= _ALBUM_NAME_WEIGHT + _ARTIST_NAME_WEIGHT - 0.05

    def test_empty_artists_list_treats_as_no_artist(self):
        r = _result("Album", artist_name="", total_tracks=0)  # no artist
        score = _score_album_search_result(r, "Album", "Some Target", file_count=0)
        # Artist sim against empty string is 0 → no artist weight contribution
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)


# ---------------------------------------------------------------------------
# Track count component
# ---------------------------------------------------------------------------


class TestTrackCountWeight:
    def test_exact_track_count_match_full_weight(self):
        r = _result("Album", total_tracks=10)
        score = _score_album_search_result(r, "Album", None, file_count=10)
        # Album (perfect) + track count (perfect) — no artist component
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT + _TRACK_COUNT_WEIGHT, abs=1e-9)

    def test_off_by_one_track_count_near_full(self):
        """1 track off out of 10 → ratio = 1.0 - 1/10 = 0.9 → 0.9 * 0.3 = 0.27"""
        r = _result("Album", total_tracks=10)
        score = _score_album_search_result(r, "Album", None, file_count=9)
        expected = _ALBUM_NAME_WEIGHT + (0.9 * _TRACK_COUNT_WEIGHT)
        assert score == pytest.approx(expected, abs=1e-9)

    def test_bandcamp_vs_streaming_track_count_mismatch(self):
        """Reporter's exact case: Bandcamp 7-track album vs Spotify
        4-track release. Track count ratio = 1.0 - 3/7 = ~0.571.
        With perfect album + artist match, total = 0.5 + 0.2 + 0.171
        = 0.871 → comfortably above the 0.4 threshold so the album
        still identifies despite the count mismatch."""
        r = _result("Work in Progress", "Godly the Ruler", total_tracks=4)
        score = _score_album_search_result(r, "Work in Progress", "Godly the Ruler", file_count=7)
        assert score > 0.4, (
            f"Bandcamp-vs-streaming case must still pass threshold; got {score:.3f}"
        )
        # Sanity bound — score should land around 0.87
        assert 0.85 < score < 0.90

    def test_zero_track_count_from_source_skips_track_component(self):
        """Some search responses don't include total_tracks. Helper
        must not penalize — just skip the track-count component."""
        r = _result("Album", total_tracks=0)
        score = _score_album_search_result(r, "Album", None, file_count=10)
        # Only album component contributes
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)

    def test_zero_file_count_skips_track_component(self):
        """Defensive: candidate has 0 files (somehow). Don't divide
        by zero or skew the score."""
        r = _result("Album", total_tracks=10)
        score = _score_album_search_result(r, "Album", None, file_count=0)
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)

    def test_huge_mismatch_track_count_no_negative_contribution(self):
        """File count 1, source track count 100 → ratio = 1 - 99/100
        = 0.01. Tiny but non-negative. `max(0, ...)` guards against
        any future formula change introducing a negative."""
        r = _result("Album", total_tracks=100)
        score = _score_album_search_result(r, "Album", None, file_count=1)
        assert score >= _ALBUM_NAME_WEIGHT  # at least the album component


# ---------------------------------------------------------------------------
# Edge cases — defensive
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_album_result_without_name_attribute(self):
        """If the result somehow lacks `.name` (unusual adapter
        return), helper falls back to '' and scores 0 album sim."""
        r = SimpleNamespace(artists=[], total_tracks=0)  # no `.name`
        score = _score_album_search_result(r, "Test Album", None, file_count=0)
        assert score == 0.0

    def test_album_result_without_artists_attribute(self):
        """If `.artists` is missing, treat as empty list."""
        r = SimpleNamespace(name="Album", total_tracks=0)  # no .artists
        score = _score_album_search_result(r, "Album", "Target Artist", file_count=0)
        # Album matches perfectly; artist sim against missing is 0
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)

    def test_album_result_with_none_total_tracks(self):
        """Some adapters return None for missing total_tracks instead
        of 0. `getattr(..., 'total_tracks', 0) or 0` should handle it."""
        r = SimpleNamespace(name="Album", artists=[], total_tracks=None)
        score = _score_album_search_result(r, "Album", None, file_count=10)
        assert score == pytest.approx(_ALBUM_NAME_WEIGHT, abs=1e-9)
