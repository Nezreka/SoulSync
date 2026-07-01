"""Regression test for the short-title penalty in
``MusicMatchingEngine.calculate_slskd_match_confidence``.

Reported pattern: searching Soulseek for an album whose title track
shares the exact same name as the album (e.g. "Py - DUMBFOUNDED" the
album containing "Py - DUMBFOUNDED - DUMBFOUNDED" the song) often
missed that one track while every other track in the same album
downloaded fine.

Root cause: the "short title" gate (added to prevent false positives
like "Love" matching "Loveless") was defined as::

    is_short_title = len(spotify_cleaned_title) <= 5 or len(title_words) == 1

The ``or len(title_words) == 1`` clause flagged *any* single-word
title regardless of its actual length — an 11-character word like
"Dumbfounded" got the same 60% confidence penalty as "Run" whenever
the artist could only be fuzzy-matched (not a clean word-boundary
hit) against the candidate's file path. Self-titled tracks are
disproportionately single, distinctive words, so this collapsed
their score below the 0.63 accept threshold while multi-word
sibling tracks in the same album — scored under identical artist-
match conditions — passed easily.

Fix: base the gate on title length alone, matching the documented
intent (the examples given — "Run", "Love", "Girls", "Stay" — are
all short strings, not merely single words).
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine
from core.download_plugins.types import TrackResult
from core.spotify_client import Track as SpotifyTrack


@pytest.fixture
def engine() -> MusicMatchingEngine:
    return MusicMatchingEngine()


def _track(name: str, artists: list[str], album: str) -> SpotifyTrack:
    return SpotifyTrack(
        id='1', name=name, artists=artists, album=album,
        duration_ms=200000, popularity=0, preview_url=None, external_urls={},
    )


def _result(filename: str, duration_ms: int = 200000) -> TrackResult:
    return TrackResult(
        username='peer', filename=filename, size=30_000_000, bitrate=1000,
        duration=duration_ms, quality='flac',
        free_upload_slots=1, upload_speed=100000, queue_length=0,
    )


# ---------------------------------------------------------------------------
# The reported scenario: self-titled long single-word track.
# ---------------------------------------------------------------------------


def test_long_single_word_self_titled_track_passes_with_weak_artist_match(engine):
    """A long single-word title (album == track name) must not be treated
    like a short/collision-prone title just because it's one word.

    "Pi" stands in for a peer whose share only fuzzy-matches "Py" (no
    clean word-boundary hit) — realistic for slightly different artist
    folder naming on Soulseek."""
    track = _track('DUMBFOUNDED', ['Py'], 'DUMBFOUNDED')
    candidate = _result('Pi/DUMBFOUNDED/01 DUMBFOUNDED.flac')

    confidence = engine.calculate_slskd_match_confidence(track, candidate)

    assert confidence > 0.63


def test_multi_word_sibling_track_passes_under_identical_conditions(engine):
    """Sanity check: a multi-word title in the same album, scored under
    the same weak-artist-match conditions, already passed before the fix
    — confirms the bug was specific to the single-word gate, not the
    weak artist match itself."""
    track = _track('Some Other Song', ['Py'], 'DUMBFOUNDED')
    candidate = _result('Pi/DUMBFOUNDED/02 Some Other Song.flac')

    confidence = engine.calculate_slskd_match_confidence(track, candidate)

    assert confidence > 0.63


# ---------------------------------------------------------------------------
# The gate must still protect genuinely short titles.
# ---------------------------------------------------------------------------


def test_genuinely_short_title_still_gated_under_weak_artist_match(engine):
    """"Run" (<=5 chars) must still get the stricter short-title scrutiny
    — the fix narrows the gate, it doesn't remove it."""
    track = _track('Run', ['Muse'], 'Origin of Symmetry')
    candidate = _result('Mu/Origin of Symmetry/Run.flac')

    confidence = engine.calculate_slskd_match_confidence(track, candidate)

    assert confidence < 0.63


def test_short_title_with_strong_artist_match_still_passes(engine):
    """A short title must still pass when the artist match IS clean —
    the gate is a penalty for weak artist matches, not an outright ban."""
    track = _track('Run', ['Muse'], 'Origin of Symmetry')
    candidate = _result('Muse/Origin of Symmetry/Run.flac')

    confidence = engine.calculate_slskd_match_confidence(track, candidate)

    assert confidence > 0.63
