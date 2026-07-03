"""Audio-completeness guard — catches files whose container duration is
correct but whose real audio is far shorter (HiFi/Monochrome truncated files:
container claims 3:08 but only ~30s actually decodes) or mostly silence. Pure
parsers are tested here; the ffmpeg call is integration.
"""

import pytest

import core.imports.silence as silence_mod
from core.imports.silence import (
    detect_broken_audio,
    is_dsd_path,
    silence_ratio_from_output,
    is_mostly_silent_reason,
    measured_duration_from_astats,
    incomplete_audio_reason,
)


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


# ── truncation: real decoded duration vs container duration ────────────────

_ASTATS_TRUNCATED = """
[Parsed_astats_0 @ 0x55] Number of samples: 1318912
[Parsed_astats_0 @ 0x55] Number of NaNs: 0
"""

_ASTATS_FULL = "[Parsed_astats_0 @ 0x55] Number of samples: 9261000\n"


def test_measured_duration_from_samples():
    # 1318912 samples / 44100 Hz ≈ 29.9s (the real Blossom file)
    assert measured_duration_from_astats(_ASTATS_TRUNCATED, 44100) == pytest.approx(29.9, abs=0.1)


def test_measured_duration_none_without_samples():
    assert measured_duration_from_astats("no stats here", 44100) is None


def test_measured_duration_none_without_sample_rate():
    assert measured_duration_from_astats(_ASTATS_TRUNCATED, 0) is None


def test_incomplete_reason_for_truncated_file():
    # 30s of real audio in a 188s container → truncated.
    reason = incomplete_audio_reason(29.9, 188.4, min_ratio=0.85)
    assert reason is not None
    assert "30s" in reason and "188s" in reason


def test_no_incomplete_reason_for_full_file():
    assert incomplete_audio_reason(187.5, 188.4, min_ratio=0.85) is None


def test_no_incomplete_reason_when_unmeasurable():
    assert incomplete_audio_reason(None, 188.4, min_ratio=0.85) is None
    assert incomplete_audio_reason(30.0, 0, min_ratio=0.85) is None


# ── DSD (#939): the samples÷rate truncation math is invalid for DSD, so it must
#    be skipped for .dsf/.dff (silence detection still applies). ──

def test_is_dsd_path():
    assert is_dsd_path("/m/Album/01. Song.dsf") is True
    assert is_dsd_path("/m/Album/01. Song.DFF") is True   # case-insensitive
    assert is_dsd_path("/m/Album/01. Song.flac") is False
    assert is_dsd_path("") is False
    assert is_dsd_path(None) is False


class _FakeProc:
    def __init__(self, stderr):
        self.stderr = stderr.encode("utf-8")


class _FakeInfo:
    length = 330.0          # container says 330s
    sample_rate = 44100


def _patch_broken_pipeline(monkeypatch, astats_stderr):
    """Make detect_broken_audio run against a canned 'truncated' ffmpeg result."""
    monkeypatch.setattr(silence_mod, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr("mutagen.File", lambda *_a, **_k: type("A", (), {"info": _FakeInfo()})())
    monkeypatch.setattr(silence_mod.subprocess, "run", lambda *_a, **_k: _FakeProc(astats_stderr))


def test_truncation_flagged_for_normal_file(monkeypatch):
    # ~40s decoded of a 330s container (12%) → a normal file IS flagged truncated.
    astats = "[Parsed_astats_0 @ 0x55] Number of samples: 1764000\n"   # 1764000/44100 ≈ 40s
    _patch_broken_pipeline(monkeypatch, astats)
    reason = detect_broken_audio("/m/Album/01. Song.flac", min_ratio=0.85)
    assert reason and "Incomplete audio" in reason


def test_truncation_skipped_for_dsd(monkeypatch):
    # Same 12%-decoding numbers, but a .dsf file must NOT be flagged — the math is
    # invalid for DSD (ffmpeg decodes DSD to PCM at a different rate). #939
    astats = "[Parsed_astats_0 @ 0x55] Number of samples: 1764000\n"
    _patch_broken_pipeline(monkeypatch, astats)
    assert detect_broken_audio("/m/Album/01. Song.dsf", min_ratio=0.85) is None
