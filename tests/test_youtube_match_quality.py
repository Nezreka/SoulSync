"""YouTube match quality (Kazimir's chaff) — the validation gate, end to end.

The old code fully exempted YouTube from the artist gate and let the
fallthrough lane re-admit anything. With apostrophe folding ("We're
Shameless" → 'were shameless'), "We Were Shameless" by a different band
scored 0.64 and downloaded. Covered here, with the REAL matching engine's
normalization + scoring so the trap is the genuine article:

  - no artist evidence → high confidence bar + no foreign title words
  - artist evidence → unchanged standard path
  - version keywords are word-boundary (no more penalizing "Staying Alive")
    and extended with react/cover/nightcore/…, enforced in BOTH lanes
  - the fallthrough lane applies the same discipline instead of none
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from core.downloads import validation
from core.downloads.validation import _title_words_are_expected, get_valid_candidates
from core.matching_engine import MusicMatchingEngine


@dataclass
class _Track:
    name: str
    artists: tuple
    duration_ms: int = 180_000
    album: Optional[str] = None


@dataclass
class _Candidate:
    title: str
    artist: str
    username: str = 'youtube'
    duration: int = 180_000
    filename: str = 'yt||x'
    confidence: float = field(default=0.0)


class _EngineForTests:
    """Real normalization + scoring; the slskd fallthrough matcher just
    hands the pool back (its internals are slskd-path-shaped and not what
    these tests pin)."""

    def __init__(self):
        self._real = MusicMatchingEngine()

    def normalize_string(self, text):
        return self._real.normalize_string(text)

    def score_track_match(self, **kwargs):
        return self._real.score_track_match(**kwargs)

    def find_best_slskd_matches_enhanced(self, track, results, **_kw):
        return list(results)


@pytest.fixture(autouse=True)
def _engine(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _EngineForTests())


def test_the_were_shameless_trap_is_rejected():
    want = _Track(name="We're Shameless", artists=('Ken Ashcorp',))
    wrong = _Candidate(title='We Were Shameless', artist='The Wrong Band')
    right = _Candidate(title="We're Shameless", artist='Ken Ashcorp')
    out = get_valid_candidates([wrong, right], want, 'ken ashcorp were shameless')
    assert right in out and wrong not in out


def test_no_artist_evidence_but_honest_title_survives_the_fallthrough():
    # A lyrics-channel upload: channel name is useless, but every word of
    # the title is the wanted title + the wanted artist + upload noise.
    want = _Track(name='I Will Be There', artists=('Art of Dying',))
    cand = _Candidate(title='I Will Be There by Art of Dying lyrics',
                      artist='LyricsWorld4U')
    out = get_valid_candidates([cand], want, 'art of dying i will be there')
    assert cand in out


def test_reaction_videos_are_rejected_in_both_lanes():
    want = _Track(name='Two Pills', artists=('TX2',))
    cand = _Candidate(title='WE REACT TO @TX2OFFICIAL - TWO PILLS - THIS WAS GREAT!!',
                      artist='Rodas React')
    assert get_valid_candidates([cand], want, 'tx2 two pills') == []


def test_a_cover_from_the_right_artist_is_still_a_cover():
    want = _Track(name='Night Eyes', artists=('The Orion Experience',))
    cand = _Candidate(title='Night Eyes | Drum Cover', artist='The Orion Experience')
    assert get_valid_candidates([cand], want, 'orion experience night eyes') == []


def test_word_boundary_keywords_stop_penalizing_staying_alive():
    want = _Track(name='Staying Alive', artists=('Bee Gees',))
    cand = _Candidate(title='Staying Alive', artist='Bee Gees')
    out = get_valid_candidates([cand], want, 'bee gees staying alive')
    assert cand in out
    assert cand.version_type != 'wrong_version'   # 'live' must not fire inside 'Alive'


def test_artist_evidence_keeps_the_standard_path():
    want = _Track(name='Shameless', artists=('Ken Ashcorp',))
    cand = _Candidate(title='Shameless', artist='Ken Ashcorp')
    out = get_valid_candidates([cand], want, 'ken ashcorp shameless')
    assert cand in out and cand.confidence >= 0.9


@pytest.mark.parametrize("title,ok", [
    ('We Were Shameless', False),                       # foreign 'we'
    ("We're Shameless (Official Audio)", True),         # wanted words + noise
    ('I Will Be There by Art of Dying lyrics', False),  # artist words NOT allowed here
    ('', False),
])
def test_title_words_are_expected_unit(title, ok):
    # expected title only — artist words are a separate allowance
    got = _title_words_are_expected(title, "We're Shameless"
                                    if 'Shameless' in title else 'I Will Be There', [])
    assert got is ok
