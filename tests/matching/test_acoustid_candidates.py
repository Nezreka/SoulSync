"""Tests for the shared AcoustID candidate-matching helper.

Issue #587 / Foxxify report — scanner used to treat ``recordings[0]``
as authoritative, so when AcoustID returned multiple candidates and
the top one was the wrong-credited recording (different MB entry
under the same fingerprint), the scanner created a false-positive
"Wrong download" finding even though a lower-ranked candidate matched
the expected metadata exactly.
"""

from __future__ import annotations

from difflib import SequenceMatcher

import pytest

from core.matching.acoustid_candidates import (
    duration_mismatches_strongly,
    find_matching_recording,
)


def _ratio_sim(a: str, b: str) -> float:
    """Reasonable test similarity that handles non-trivial differences."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ──────────────────────────────────────────────────────────────────────
# find_matching_recording
# ──────────────────────────────────────────────────────────────────────

def test_top_recording_matches_returns_immediately():
    recordings = [
        {'title': 'Nana', 'artist': 'Geoxor'},
        {'title': 'Nana', 'artist': 'Edward Vesala Trio'},
    ]
    result, t_sim, a_sim = find_matching_recording(
        recordings, 'Nana', 'Geoxor', similarity=_ratio_sim,
    )
    assert result == {'title': 'Nana', 'artist': 'Geoxor'}
    assert t_sim == 1.0
    assert a_sim == 1.0


def test_falls_through_to_lower_ranked_match_for_foxxify_nana_case():
    """Reporter case 2: top AcoustID candidate is 'Nana' by 'Edward
    Vesala Trio' (97% fingerprint), but the LOWER-ranked candidate
    is the expected 'Nana' by 'Geoxor'. Pre-fix scanner saw only [0]
    and flagged. Post-fix returns the matching candidate."""
    recordings = [
        {'title': 'Nana', 'artist': 'Edward Vesala Trio'},  # AcoustID's top match
        {'title': 'Nana', 'artist': 'Geoxor'},              # the actual right one
    ]
    result, _, _ = find_matching_recording(
        recordings, 'Nana', 'Geoxor', similarity=_ratio_sim,
    )
    assert result == {'title': 'Nana', 'artist': 'Geoxor'}


def test_no_match_returns_none_with_best_seen_sims():
    """When no candidate passes thresholds, return the best-seen sims
    so callers can log the closest near-miss in the finding."""
    recordings = [
        {'title': 'Different Song', 'artist': 'Different Artist'},
        {'title': 'Sort Of Close', 'artist': 'Different Artist'},
    ]
    result, t_sim, a_sim = find_matching_recording(
        recordings, 'Different', 'AnotherArtist',
        similarity=_ratio_sim,
        title_threshold=0.95,
        artist_threshold=0.95,
    )
    assert result is None
    # Best seen — even though no candidate passed the threshold
    assert t_sim > 0.0
    assert a_sim > 0.0


def test_skips_recordings_missing_title_or_artist():
    recordings = [
        {'title': None, 'artist': 'Geoxor'},
        {'title': 'Nana', 'artist': ''},
        {'title': 'Nana', 'artist': 'Geoxor'},
    ]
    result, _, _ = find_matching_recording(
        recordings, 'Nana', 'Geoxor', similarity=_ratio_sim,
    )
    assert result == {'title': 'Nana', 'artist': 'Geoxor'}


def test_skips_non_dict_entries():
    recordings = [None, 'string', {'title': 'Nana', 'artist': 'Geoxor'}]
    result, _, _ = find_matching_recording(
        recordings, 'Nana', 'Geoxor', similarity=_ratio_sim,
    )
    assert result == {'title': 'Nana', 'artist': 'Geoxor'}


def test_empty_inputs_return_none():
    assert find_matching_recording([], 'X', 'Y')[0] is None
    assert find_matching_recording([{'title': 'X', 'artist': 'Y'}], '', 'Y')[0] is None
    assert find_matching_recording([{'title': 'X', 'artist': 'Y'}], 'X', '')[0] is None


def test_separate_artist_similarity_function_is_honored():
    """Verifier passes alias-aware comparison via artist_similarity.
    Make sure it's used instead of the generic similarity."""
    recordings = [{'title': 'Track', 'artist': '澤野弘之'}]

    def alias_aware(expected, actual):
        # Pretend our alias chain bridges Hiroyuki Sawano ↔ 澤野弘之
        if expected == 'Hiroyuki Sawano' and actual == '澤野弘之':
            return 1.0
        return 0.0

    result, _, a_sim = find_matching_recording(
        recordings, 'Track', 'Hiroyuki Sawano',
        similarity=_ratio_sim,
        artist_similarity=alias_aware,
    )
    assert result is not None
    assert a_sim == 1.0


def test_skip_predicate_drops_unwanted_candidates():
    """Verifier uses skip_predicate to drop wrong-version recordings
    (instrumental vs vocal, etc.)."""
    recordings = [
        {'title': 'Track (Instrumental)', 'artist': 'X'},
        {'title': 'Track', 'artist': 'X'},
    ]
    result, _, _ = find_matching_recording(
        recordings, 'Track', 'X',
        similarity=_ratio_sim,
        skip_predicate=lambda r: 'instrumental' in (r.get('title') or '').lower(),
    )
    assert result == {'title': 'Track', 'artist': 'X'}


def test_title_threshold_can_be_lowered_for_loose_matching():
    recordings = [{'title': 'Sort Of Close', 'artist': 'Right Artist'}]
    # With strict default threshold this fails
    result_strict, _, _ = find_matching_recording(
        recordings, 'Different', 'Right Artist', similarity=_ratio_sim,
    )
    assert result_strict is None
    # With a permissive threshold the artist match alone wouldn't help —
    # title sim must also pass.
    result_loose, _, _ = find_matching_recording(
        recordings, 'Different', 'Right Artist',
        similarity=_ratio_sim, title_threshold=0.0,
    )
    assert result_loose is not None


# ──────────────────────────────────────────────────────────────────────
# duration_mismatches_strongly — guard against fingerprint collisions
# ──────────────────────────────────────────────────────────────────────

def test_durations_within_tolerance_pass():
    # 3-minute track, 1-second drift — well within tolerance
    assert duration_mismatches_strongly(180, 181) is False
    # 3-minute vs 4-minute — within the 60s absolute tolerance
    assert duration_mismatches_strongly(180, 240) is False


def test_drift_above_absolute_floor_flags():
    # 3-minute expected, 5-minute candidate (120s drift > 63s threshold)
    assert duration_mismatches_strongly(180, 300) is True


def test_relative_tolerance_scales_with_long_tracks():
    # 30-minute expected vs 12-minute candidate (1080s vs 720s) —
    # 18-minute drift > 35% of 30min = 10.5min → mismatch
    assert duration_mismatches_strongly(1800, 720) is True
    # 30-minute expected vs 28-minute candidate — 2min drift = under
    # max(60s, 35%*30min) = max(60, 630) = 630s → still safe
    assert duration_mismatches_strongly(1800, 1680) is False


def test_reporter_17min_mashup_vs_5min_track_flagged():
    """Foxxify's 17min mashup edit vs 5min late-70s Japanese hiphop —
    fingerprint collision. Duration guard should mark this suspicious."""
    assert duration_mismatches_strongly(17 * 60, 5 * 60) is True


def test_unknown_duration_returns_false_no_behavior_change():
    """When either side is missing duration, don't change behavior."""
    assert duration_mismatches_strongly(None, 300) is False
    assert duration_mismatches_strongly(180, None) is False
    assert duration_mismatches_strongly(0, 300) is False
    assert duration_mismatches_strongly(180, 0) is False
    assert duration_mismatches_strongly(-5, 300) is False


def test_string_or_int_durations_handled():
    # Defensive — coerce numeric types
    assert duration_mismatches_strongly(180.5, 181.0) is False
    assert duration_mismatches_strongly(int(180), int(300)) is True
