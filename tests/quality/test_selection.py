"""core.quality.selection — quality-aware ranking + the satisfied flag that
drives the engine's source fall-through.

A source is "satisfied" when at least one of its candidates meets a real
target (strict, fallback off). The engine uses that to decide whether to
stop on the current source or escalate to the next.
"""

import pytest

from core.quality.model import AudioQuality, QualityTarget
from core.quality.selection import rank_with_targets


class _Cand:
    """Minimal candidate: filter_and_rank only needs ``.audio_quality``."""
    def __init__(self, aq, name=""):
        self.audio_quality = aq
        self.name = name

    def __repr__(self):
        return f"_Cand({self.name})"


FLAC_HIRES = AudioQuality('flac', sample_rate=96000, bit_depth=24)
FLAC_CD = AudioQuality('flac', sample_rate=44100, bit_depth=16)
MP3_320 = AudioQuality('mp3', bitrate=320)

WANT_HIRES = [QualityTarget(label='FLAC 24', format='flac', bit_depth=24, min_sample_rate=96000)]
WANT_FLAC_ONLY = [QualityTarget(label='FLAC 16', format='flac', bit_depth=16)]


def test_satisfied_when_a_candidate_meets_a_target():
    cands = [_Cand(MP3_320, 'mp3'), _Cand(FLAC_HIRES, 'hires')]
    ranked, satisfied = rank_with_targets(cands, WANT_HIRES, fallback_enabled=True)
    assert satisfied is True
    assert ranked[0].name == 'hires'  # the matching candidate wins


def test_unsatisfied_but_fallback_returns_sorted_when_enabled():
    cands = [_Cand(MP3_320, 'mp3')]
    ranked, satisfied = rank_with_targets(cands, WANT_FLAC_ONLY, fallback_enabled=True)
    assert satisfied is False           # no FLAC → no target met
    assert [c.name for c in ranked] == ['mp3']  # but fallback keeps it


def test_unsatisfied_and_fallback_off_returns_empty():
    cands = [_Cand(MP3_320, 'mp3')]
    ranked, satisfied = rank_with_targets(cands, WANT_FLAC_ONLY, fallback_enabled=False)
    assert satisfied is False
    assert ranked == []


def test_empty_targets_accepts_everything_satisfied():
    cands = [_Cand(MP3_320, 'mp3'), _Cand(FLAC_CD, 'cd')]
    ranked, satisfied = rank_with_targets(cands, [], fallback_enabled=True)
    assert satisfied is True            # no constraint → first source wins
    assert ranked[0].name == 'cd'       # still quality-sorted


def test_no_candidates_is_unsatisfied():
    ranked, satisfied = rank_with_targets([], WANT_FLAC_ONLY, fallback_enabled=True)
    assert satisfied is False
    assert ranked == []
