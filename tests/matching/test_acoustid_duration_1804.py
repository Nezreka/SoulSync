"""#1804 (QT3496): acoustid false positives on correct tiesto / saxon files.

three things were wrong, each pinned here:
  - pyacoustid drops the per-recording duration acoustid sends, so every
    duration guard read None and never fired (the scan's collision guard was
    dead code, import had none).
  - musicbrainz files remixes and extended mixes under the bare title, so a
    file that runs the remix's length was failed as "the original".
  - a fingerprint collision (every top match a wildly different length) could
    still fail a file at import.
"""

from __future__ import annotations

import sys
import types
import wave

import pytest

import core.acoustid_client as acc
from core.acoustid_client import AcoustIDClient, parse_lookup_with_duration
from core.acoustid_verification import AcoustIDVerification, VerificationResult
from core.matching.acoustid_candidates import (
    durations_agree,
    fingerprint_length_contradicts,
)
from core.matching.audio_verification import Decision, evaluate


def _rec(title, artist, duration=None, score=0.95, mbid=None):
    return {'mbid': mbid or f'{title}-{artist}-{duration}', 'title': title,
            'artist': artist, 'score': score, 'duration': duration}


# ── the client keeps acoustid's duration ─────────────────────────────────────

def _raw(*recordings, score=0.95):
    return {'status': 'ok', 'results': [{'score': score, 'id': 'r1', 'recordings': list(recordings)}]}


def test_parser_keeps_recording_duration():
    data = _raw({'id': 'm1', 'title': 'Suburban Train', 'duration': 627,
                 'artists': [{'name': 'Tiësto'}]})
    assert list(parse_lookup_with_duration(data)) == [
        (0.95, 'm1', 'Suburban Train', 'Tiësto', 627)]


@pytest.mark.parametrize('bad', [None, 0, -4, 'long', True])
def test_parser_unusable_duration_is_none(bad):
    data = _raw({'id': 'm1', 'title': 'x', 'duration': bad})
    assert list(parse_lookup_with_duration(data))[0][4] is None


def test_parser_skips_results_without_recordings():
    data = {'status': 'ok', 'results': [{'score': 0.9, 'id': 'r1'}]}
    assert list(parse_lookup_with_duration(data)) == []


def _client(monkeypatch, raw):
    fake = types.ModuleType('acoustid')
    for name in ('WebServiceError', 'NoBackendError', 'FingerprintGenerationError'):
        setattr(fake, name, type(name, (Exception,), {}))
    fake.parse_lookup_result = lambda data: pytest.fail('pyacoustid parser drops duration')
    fake.match = lambda api_key, path, parse=True: raw
    monkeypatch.setitem(sys.modules, 'acoustid', fake)
    monkeypatch.setattr(acc, 'ACOUSTID_AVAILABLE', True)
    client = AcoustIDClient()
    client._api_key = 'testkey123'
    return client


def test_lookup_with_status_carries_duration_on_each_recording(tmp_path, monkeypatch):
    audio = tmp_path / 'a.flac'
    audio.write_bytes(b'x')
    client = _client(monkeypatch, _raw(
        {'id': 'm1', 'title': 'Flex', 'duration': 234, 'artists': [{'name': 'Spag Heddy'}]}))
    out = client.lookup_with_status(str(audio))
    assert out['status'] == 'ok'
    assert out['recordings'][0]['duration'] == 234


def test_lookup_with_status_bad_status_is_still_an_error(tmp_path, monkeypatch):
    audio = tmp_path / 'a.flac'
    audio.write_bytes(b'x')
    client = _client(monkeypatch, {'status': 'ok'})
    assert client.lookup_with_status(str(audio))['status'] == 'error'


# ── version gate: the file's length backs the version asked for ─────────────

def test_remix_filed_under_bare_title_skips_when_file_runs_the_remix_length():
    """suburban train (way out west remix): musicbrainz has dozens of bare
    'suburban train' recordings, the 8+ minute file was failed as original."""
    recs = [_rec('Suburban Train', 'Tiësto', duration=520)]
    out = evaluate('Suburban Train (Way Out West Remix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=521.0,
                   expected_duration_s=519.0)
    assert out.decision == Decision.SKIP, out.reason


def test_without_lengths_the_version_gate_is_unchanged():
    recs = [_rec('Suburban Train', 'Tiësto', duration=520)]
    out = evaluate('Suburban Train (Way Out West Remix)', 'Tiësto', recs,
                   fingerprint_score=0.95)
    assert out.decision == Decision.FAIL


def test_file_not_running_the_expected_length_still_fails():
    """asked for the 8:40 remix, got a 3:30 file acoustid calls the original.
    that's the wrong download the version gate exists for."""
    recs = [_rec('Suburban Train', 'Tiësto', duration=210)]
    out = evaluate('Suburban Train (Way Out West Remix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=210.0,
                   expected_duration_s=520.0)
    assert out.decision == Decision.FAIL


def test_extended_mix_mislinked_to_radio_edit_skips():
    """parachute (extended mix): acoustid's match is a 3:28 radio edit, the
    file runs the 8 minute extended length. the label belongs to another cut."""
    recs = [_rec('Parachute (Radio Edit)', 'Tiësto', duration=208)]
    out = evaluate('Parachute (Extended Mix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=482.0,
                   expected_duration_s=480.0)
    assert out.decision == Decision.SKIP, out.reason


def test_mislinked_label_skips_even_when_collision_cant_explain_it():
    """same entry also links a bare full-length recording, so it's no
    collision. the radio edit label still belongs to a 3:28 cut, not this file."""
    recs = [_rec('Parachute (Radio Edit)', 'Tiësto', duration=208),
            _rec('Parachute', 'Tiësto', duration=481)]
    assert not fingerprint_length_contradicts(recs, 482.0)
    out = evaluate('Parachute (Extended Mix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=482.0,
                   expected_duration_s=480.0)
    assert out.decision == Decision.SKIP, out.reason


def test_labelled_version_matching_the_file_length_still_fails():
    """a radio edit that really runs the file's length is the file. two sided
    mismatch with no length contradiction stays strict."""
    recs = [_rec('Parachute (Radio Edit)', 'Tiësto', duration=481)]
    out = evaluate('Parachute (Extended Mix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=482.0,
                   expected_duration_s=480.0)
    assert out.decision == Decision.FAIL


def test_live_below_the_live_bar_skips_when_lengths_agree():
    """ace of spades (with fast eddie clarke, live in london 2016) by saxon:
    musicbrainz titles it 'ace of spades (london 2016)', no live marker, and a
    0.83 fingerprint is under the live loosening's 0.85 bar."""
    recs = [_rec('Ace of Spades (London 2016)', 'Saxon', duration=184, score=0.83)]
    out = evaluate('Ace of Spades (with Fast Eddie Clarke - Live in London 2016)',
                   'Saxon', recs, fingerprint_score=0.83,
                   file_duration_s=185.0, expected_duration_s=184.0)
    assert out.decision == Decision.SKIP, out.reason


def test_length_rescue_never_covers_a_different_song():
    recs = [_rec('Red Lights (Radio Edit)', 'Tiësto', duration=520)]
    out = evaluate('Suburban Train (Way Out West Remix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=520.0,
                   expected_duration_s=520.0)
    assert out.decision == Decision.FAIL


def test_length_rescue_never_covers_a_different_artist():
    recs = [_rec('Suburban Train', 'Spag Heddy', duration=520)]
    out = evaluate('Suburban Train (Way Out West Remix)', 'Tiësto', recs,
                   fingerprint_score=0.95, file_duration_s=520.0,
                   expected_duration_s=520.0)
    assert out.decision == Decision.FAIL


# ── fingerprint collision guard ──────────────────────────────────────────────

def test_artist_mismatch_with_every_top_match_a_different_length_skips():
    recs = [_rec('Flex', 'Spag Heddy', duration=600)]
    out = evaluate('Flex', 'Tiësto', recs, fingerprint_score=0.95,
                   file_duration_s=212.0)
    assert out.decision == Decision.SKIP, out.reason


def test_artist_mismatch_with_a_compatible_length_still_fails():
    """flex by spag heddy is 3:54, tiesto's is 3:32. close lengths prove
    nothing, so the real wrong-artist verdict stands."""
    recs = [_rec('Flex', 'Spag Heddy', duration=234)]
    out = evaluate('Flex', 'Tiësto', recs, fingerprint_score=0.95,
                   file_duration_s=212.0)
    assert out.decision == Decision.FAIL


def test_collision_needs_every_tied_top_match_to_disagree():
    recs = [_rec('Flex', 'Spag Heddy', duration=600),
            _rec('Flex (VIP)', 'Spag Heddy', duration=214)]
    assert not fingerprint_length_contradicts(recs, 212.0)
    out = evaluate('Flex', 'Tiësto', recs, fingerprint_score=0.95,
                   file_duration_s=212.0)
    assert out.decision == Decision.FAIL


def test_lower_scored_lengths_dont_count():
    recs = [_rec('Flex', 'Spag Heddy', duration=600, score=0.95),
            _rec('Flex', 'Spag Heddy', duration=212, score=0.60)]
    assert fingerprint_length_contradicts(recs, 212.0)


def test_unknown_lengths_never_contradict():
    assert not fingerprint_length_contradicts([_rec('Flex', 'x')], 212.0)
    assert not fingerprint_length_contradicts([_rec('Flex', 'x', 600)], None)


def test_collision_guard_never_touches_a_pass():
    recs = [_rec('Flex', 'Tiësto', duration=600)]
    out = evaluate('Flex', 'Tiësto', recs, fingerprint_score=0.95,
                   file_duration_s=212.0)
    assert out.decision == Decision.PASS


def test_durations_agree_is_tight():
    assert durations_agree(480, 482)
    assert durations_agree(600, 611)      # 2% of a long mix
    assert not durations_agree(480, 490)
    assert not durations_agree(None, 480)
    assert not durations_agree(480, 0)


# ── verify_audio_file reads the real file length ─────────────────────────────

def _wav(path, seconds, rate=100):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b'\x80' * int(seconds * rate))


class _Client:
    def __init__(self, recordings):
        self._recs = recordings

    def is_available(self):
        return True, 'ok'

    def lookup_with_status(self, path):
        return {'status': 'ok', 'recordings': [dict(r) for r in self._recs],
                'best_score': max(r['score'] for r in self._recs)}


@pytest.fixture(autouse=True)
def _no_musicbrainz(monkeypatch):
    import core.acoustid_verification as av
    monkeypatch.setattr(av, '_resolve_expected_artist_aliases', lambda name: [])
    monkeypatch.setattr(av, '_resolve_alternate_titles', lambda recs: False)


def _verifier(recordings):
    v = AcoustIDVerification.__new__(AcoustIDVerification)
    v.acoustid_client = _Client(recordings)
    return v


def test_verify_audio_file_uses_file_length_and_expected_length(tmp_path):
    audio = tmp_path / 'suburban.wav'
    _wav(audio, 520)
    v = _verifier([_rec('Suburban Train', 'Tiësto', duration=520)])
    result, msg = v.verify_audio_file(
        str(audio), 'Suburban Train (Way Out West Remix)', 'Tiësto', {},
        expected_duration_s=519.0)
    assert result == VerificationResult.SKIP, msg
    # same file, no expected length handed in: the old strict verdict
    result, _ = v.verify_audio_file(
        str(audio), 'Suburban Train (Way Out West Remix)', 'Tiësto', {})
    assert result == VerificationResult.FAIL


def test_verify_audio_file_collision_from_real_length(tmp_path):
    audio = tmp_path / 'flex.wav'
    _wav(audio, 212)
    v = _verifier([_rec('Flex', 'Spag Heddy', duration=600)])
    result, msg = v.verify_audio_file(str(audio), 'Flex', 'Tiësto', {})
    assert result == VerificationResult.SKIP, msg


def test_verify_audio_file_unreadable_file_keeps_old_verdict(tmp_path):
    audio = tmp_path / 'flex.flac'
    audio.write_bytes(b'not audio')
    v = _verifier([_rec('Flex', 'Spag Heddy', duration=600)])
    result, _ = v.verify_audio_file(str(audio), 'Flex', 'Tiësto', {})
    assert result == VerificationResult.FAIL
