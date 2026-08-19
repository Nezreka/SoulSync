"""Radio edits were being thrown away, including when they were asked for.

``detect_version_type`` walks a dict of version patterns and stops at the first
hit. ``\\bedit\\b`` is one of the *remix* patterns and remix sat first, so
"Radio Edit" and "Clean Edit" came back classified as remixes. Remix is one of
the four reject-on-sight types, so those files scored 0.0 and were dropped —
even when the source title named the radio edit itself:

    want 'Song'              vs 'Song (Radio Edit).mp3'  -> 0.0 rejected
    want 'Song - Radio Edit' vs 'Song (Radio Edit).mp3'  -> 0.0 rejected
    want 'Song (Radio Edit)' vs 'Song (Radio Edit).flac' -> 0.0 rejected

The 'radio' entry with its gentle 0.08 penalty was unreachable, and so was the
``\\bclean\\s*edit\\b`` pattern living inside it.

Fix is an ordering one: 'radio' moves ahead of 'remix'. That was chosen as the
smallest possible surface — the only strings whose classification can change are
ones matching a radio pattern that previously fell to remix. Moving 'remix'
later instead would have re-labelled "Live Remix", "Acoustic Edit" and friends,
which is a much wider blast radius for no benefit.

Deliberately NOT touched: "(Club Mix)" still classifies as 'original', which is
wrong. Naming it a remix would start rejecting club mixes that download fine
today — a regression traded for a cosmetic correction.
"""

from types import SimpleNamespace

import pytest

import core.matching_engine as me
from core.matching_engine import MusicMatchingEngine


class _Config:
    """Explicit preference off, so only the version handling is under test."""

    def get(self, key, default=None):
        return default


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch):
    monkeypatch.setattr(me, 'config_manager', _Config())


@pytest.fixture
def engine():
    return MusicMatchingEngine()


def _score(engine, wanted, filename, base=0.90):
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    return engine.calculate_slskd_match_confidence_enhanced(
        SimpleNamespace(name=wanted), SimpleNamespace(filename=filename))


# ── the misclassification ────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", [
    'Artist - Song (Radio Edit).mp3',
    'Artist - Song [Radio Edit].flac',
    'Artist - Song - Radio Edit.mp3',
    '01 Song (radio edit).mp3',
])
def test_radio_edit_is_radio_not_remix(engine, filename):
    assert engine.detect_version_type(filename) == ('radio', 0.08)


def test_clean_edit_reaches_the_radio_entry(engine):
    """'\\bclean\\s*edit\\b' lives in the radio bucket. remix's '\\bedit\\b'
    fired first and made that pattern dead code."""
    assert engine.detect_version_type('Artist - Song (Clean Edit).mp3')[0] == 'radio'


def test_radio_version_still_radio(engine):
    assert engine.detect_version_type('Artist - Song (Radio Version).mp3')[0] == 'radio'


# ── the user-visible failure ─────────────────────────────────────────────────

@pytest.mark.parametrize("wanted", ['Song', 'Song - Radio Edit', 'Song (Radio Edit)'])
def test_radio_edit_is_no_longer_thrown_away(engine, wanted):
    confidence, version = _score(engine, wanted, 'Artist - Song (Radio Edit).mp3')
    assert version == 'radio'
    assert confidence > 0.58, "a radio edit must survive the confidence cut"


# ── nothing else moved ───────────────────────────────────────────────────────

@pytest.mark.parametrize("filename,expected", [
    ('Artist - Song.flac', 'original'),
    ('Artist - Song (Remix).mp3', 'remix'),
    ('Artist - Song (DJ Rmx).mp3', 'remix'),
    ('Artist - Song (Live at Wembley).flac', 'live'),
    ('Artist - Song (Acoustic).flac', 'acoustic'),
    ('Artist - Song (Instrumental).flac', 'instrumental'),
    ('Artist - Song (Extended Mix).flac', 'extended'),
    ('Artist - Song (Clean).mp3', 'clean'),
    ('Artist - Song (Explicit).flac', 'explicit'),
    ('Artist - Song (Demo).mp3', 'demo'),
    # left alone on purpose — see the module docstring
    ('Artist - Song (Club Mix).mp3', 'original'),
])
def test_other_classifications_unchanged(engine, filename, expected):
    assert engine.detect_version_type(filename)[0] == expected


@pytest.mark.parametrize("version_word", ['Live', 'Remix', 'Acoustic', 'Instrumental'])
def test_reject_on_sight_versions_still_rejected(engine, version_word):
    """The guard this fix must not loosen: a version the source never asked for
    is still dropped outright."""
    confidence, version = _score(engine, 'Song', f'Artist - Song ({version_word}).flac')
    assert confidence == 0.0
    assert version == 'rejected_version_mismatch'
