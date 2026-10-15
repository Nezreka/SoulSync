"""Slowed / sped-up / nightcore cuts are versions of their own.

``detect_version_type`` had no label for them, so "Devil Eyes - Slowed" and
the plain "Devil Eyes" both read as 'original'. The Soulseek scorer gave the
studio file 0.73 for the slowed request, and AcoustID's version gate passed it
because the fingerprinted recording "Devil Eyes" was 'original' too. The
studio cut was imported as the slowed track.
"""

from types import SimpleNamespace

import pytest

import core.matching_engine as me
from core.acoustid_verification import AcoustIDVerification, VerificationResult
from core.matching_engine import MusicMatchingEngine


class _Config:
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


@pytest.mark.parametrize("title,expected", [
    ('Devil Eyes - Slowed', 'slowed'),
    ('Song (Slowed + Reverb)', 'slowed'),
    ('Song (Super Slowed)', 'slowed'),
    ('Song (Slowed Edit)', 'slowed'),
    ('Song (Sped Up)', 'sped_up'),
    ('Song - Speed Up', 'sped_up'),
    ('Song (Nightcore)', 'sped_up'),
    ('Slow Down', 'original'),
])
def test_tempo_edits_are_classified(engine, title, expected):
    assert engine.detect_version_type(title)[0] == expected


@pytest.mark.parametrize("wanted,filename", [
    ('Devil Eyes - Slowed', r'Music\ZODIVK\ZODIVK - Devil Eyes.flac'),
    ('Devil Eyes', r'Music\ZODIVK\ZODIVK - Devil Eyes (Slowed).flac'),
    ('Devil Eyes - Slowed', r'Music\ZODIVK\ZODIVK - Devil Eyes (Sped Up).flac'),
])
def test_scorer_rejects_the_other_tempo_in_both_directions(engine, wanted, filename):
    assert _score(engine, wanted, filename) == (0.0, 'rejected_version_mismatch')


def test_scorer_keeps_the_asked_for_tempo_edit(engine):
    confidence, version = _score(engine, 'Devil Eyes - Slowed',
                                 r'Music\ZODIVK\ZODIVK - Devil Eyes (Slowed).flac')
    assert version == 'slowed'
    assert confidence > 0.58


def test_acoustid_fails_the_studio_cut_for_a_slowed_request():
    verifier = AcoustIDVerification()
    verifier.acoustid_client = SimpleNamespace(
        is_available=lambda: (True, 'available'),
        lookup_with_status=lambda path: {
            'recordings': [{'title': 'Devil Eyes', 'artist': 'ZODIVK'}],
            'best_score': 0.99, 'recording_mbids': [],
        },
    )
    result, msg = verifier.verify_audio_file('/fake.flac', 'Devil Eyes - Slowed', 'ZODIVK')
    assert result == VerificationResult.FAIL
    assert 'version mismatch' in msg.lower()
