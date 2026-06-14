"""Silence guard — catches files whose container duration is correct but whose
audio is mostly silence (e.g. HiFi/Monochrome 30s-preview padded out to the
full track length). Pure parser is tested here; the ffmpeg call is integration.
"""

import pytest

from core.imports.silence import silence_ratio_from_output, is_mostly_silent_reason


_ONE_LONG_TAIL = """
Input #0, flac, from 'song.flac':
[silencedetect @ 0x55] silence_start: 31.512
[silencedetect @ 0x55] silence_end: 210.300 | silence_duration: 178.788
"""

_TWO_GAPS = """
[silencedetect @ 0x55] silence_start: 0
[silencedetect @ 0x55] silence_end: 1.5 | silence_duration: 1.5
[silencedetect @ 0x55] silence_start: 200
[silencedetect @ 0x55] silence_end: 203 | silence_duration: 3.0
"""

_NO_SILENCE = "Input #0, flac\n[some other ffmpeg chatter]\n"


def test_ratio_single_long_trailing_silence():
    r = silence_ratio_from_output(_ONE_LONG_TAIL, total_duration_s=210.3)
    assert r == pytest.approx(178.788 / 210.3, rel=1e-3)
    assert r > 0.8


def test_ratio_sums_multiple_silences():
    r = silence_ratio_from_output(_TWO_GAPS, total_duration_s=210.0)
    assert r == pytest.approx(4.5 / 210.0, rel=1e-3)


def test_ratio_no_silence_is_zero():
    assert silence_ratio_from_output(_NO_SILENCE, total_duration_s=210.0) == 0.0


def test_ratio_zero_duration_is_zero():
    assert silence_ratio_from_output(_ONE_LONG_TAIL, total_duration_s=0) == 0.0


def test_ratio_capped_at_one():
    # Defensive: bogus silence longer than the track can't exceed 1.0.
    out = "[silencedetect @ 0x] silence_end: 999 | silence_duration: 999\n"
    assert silence_ratio_from_output(out, total_duration_s=210.0) == 1.0


def test_reason_when_mostly_silent():
    reason = is_mostly_silent_reason(_ONE_LONG_TAIL, total_duration_s=210.3, threshold=0.5)
    assert reason is not None
    assert "silent" in reason.lower()


def test_no_reason_for_normal_song():
    assert is_mostly_silent_reason(_TWO_GAPS, total_duration_s=210.0, threshold=0.5) is None
