"""Tests for core/downloads/validation.py — SoundCloud preview filter.

The SoundCloud anonymous tier serves a ~30s preview clip for tracks
gated behind Go+ / login. ``filter_soundcloud_previews`` drops these
candidates before they reach the matcher, the modal cache, or the
manual-pick download path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.downloads.validation import filter_soundcloud_previews


@dataclass
class _Track:
    duration_ms: int


@dataclass
class _Candidate:
    username: str
    duration: Optional[int]  # milliseconds
    title: str = ''


def test_drops_soundcloud_30s_preview_when_expected_long():
    """A 30s SC candidate against a 5-minute expected track is the
    canonical preview-snippet case — must be dropped."""
    expected = _Track(duration_ms=338_000)  # ~5:38
    cands = [
        _Candidate(username='soundcloud', duration=30_000, title='Preview'),
        _Candidate(username='soundcloud', duration=338_000, title='Real'),
    ]
    result = filter_soundcloud_previews(cands, expected)
    assert len(result) == 1
    assert result[0].title == 'Real'


def test_drops_under_half_expected_duration():
    """SC candidate at 100s against 300s expected = clearly truncated /
    wrong content. Must be dropped even if not at the 30s boundary."""
    expected = _Track(duration_ms=300_000)
    cand = _Candidate(username='soundcloud', duration=100_000)
    assert filter_soundcloud_previews([cand], expected) == []


def test_keeps_soundcloud_when_expected_track_is_short():
    """Genuinely short SC tracks (intros, sound effects, sub-minute
    songs) must pass through when the expected track is also short.
    Filter only kicks in when expected > 60s."""
    expected = _Track(duration_ms=45_000)  # 45s expected
    cand = _Candidate(username='soundcloud', duration=30_000)
    result = filter_soundcloud_previews([cand], expected)
    assert result == [cand]


def test_does_not_filter_non_soundcloud_sources():
    """A 30s candidate from another streaming source isn't a SoundCloud
    preview — leave it for the generic matching engine to score."""
    expected = _Track(duration_ms=338_000)
    yt = _Candidate(username='youtube', duration=30_000)
    tidal = _Candidate(username='tidal', duration=30_000)
    assert filter_soundcloud_previews([yt, tidal], expected) == [yt, tidal]


def test_returns_input_unchanged_without_expected_duration():
    """Without a Spotify-track / expected duration we can't reason
    about previews — pass everything through."""
    cands = [
        _Candidate(username='soundcloud', duration=30_000),
        _Candidate(username='soundcloud', duration=300_000),
    ]
    assert filter_soundcloud_previews(cands, None) == cands
    assert filter_soundcloud_previews(cands, _Track(duration_ms=0)) == cands


def test_empty_input_returns_empty_list():
    assert filter_soundcloud_previews([], _Track(duration_ms=200_000)) == []


def test_keeps_soundcloud_candidate_at_threshold():
    """Boundary check: 35s candidate against 200s expected — exactly
    at the 35s preview boundary, but 35s is also above
    expected*0.5 (100s) check (35 < 100, so still drops). Use a
    higher value to confirm the just-above threshold passes."""
    expected = _Track(duration_ms=200_000)  # 200s
    # 110s passes both checks: > 35s AND > 100s (half of 200s)
    cand = _Candidate(username='soundcloud', duration=110_000)
    assert filter_soundcloud_previews([cand], expected) == [cand]
