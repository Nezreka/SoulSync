"""Sample Studio — analysis performance regression tests.

Locks in the v2 analysis design: downsampled analysis sample rate, a single
shared onset envelope (beat tracking must NOT recompute it), and a warmup
that never breaks the honest ImportError path. Uses a fake librosa so the
tests assert the wiring without paying real DSP cost.
"""

import types

import numpy as np
import pytest
import soundfile as sf

from core.sample import analyze as sample_analyze

SR = 44100


def _tone_wav(path, seconds=2.0):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    y = (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sf.write(str(path), y, SR, subtype="PCM_16")


def _fake_librosa(calls):
    onset = types.SimpleNamespace(
        onset_strength=lambda y, sr: calls.setdefault("envelope", ("ENV", y, sr))[0]
    )
    util = types.SimpleNamespace(
        peak_pick=lambda env, **kw: calls.update(peak_kw=kw) or np.array([10, 20])
    )
    beat = types.SimpleNamespace(
        beat_track=lambda **kw: calls.update(beat_kw=kw) or (np.array([100.0]), None)
    )

    def frames_to_time(frames, sr):
        calls["frames_sr"] = sr
        return np.array([float(f) * 512 / sr for f in frames])

    def resample(y, orig_sr, target_sr):
        calls["resample"] = (orig_sr, target_sr)
        return y

    return types.SimpleNamespace(
        onset=onset, util=util, beat=beat,
        frames_to_time=frames_to_time, resample=resample,
    )


def test_analyze_downsamples_and_shares_envelope(tmp_path, monkeypatch):
    """The perf-sensitive wiring: 44100 Hz -> resample to 22050, one envelope
    computed and handed to beat_track (no second STFT inside beat_track)."""
    wav = tmp_path / "tone.wav"
    _tone_wav(wav)
    calls = {}
    monkeypatch.setattr(
        sample_analyze, "_load_librosa", lambda: _fake_librosa(calls)
    )
    result = sample_analyze.analyze_track(str(wav))

    assert calls["resample"] == (44100, 22050), calls.get("resample")
    # onset_strength ran at the analysis rate…
    assert calls["envelope"][2] == 22050
    # …and beat_track got THAT envelope instead of raw audio.
    beat_kw = calls["beat_kw"]
    assert "y" not in beat_kw, "beat_track must not receive raw audio"
    assert beat_kw["onset_envelope"] == "ENV"
    assert beat_kw["sr"] == 22050
    assert result["bpm"] == 100.0
    assert result["analyzer_version"] == sample_analyze.ANALYZER_VERSION


def test_analyze_skips_resample_when_already_22050(tmp_path, monkeypatch):
    n = 22050
    y = np.zeros(n, dtype=np.float32)
    wav = tmp_path / "tone22.wav"
    sf.write(str(wav), y, 22050, subtype="PCM_16")
    calls = {}
    monkeypatch.setattr(
        sample_analyze, "_load_librosa", lambda: _fake_librosa(calls)
    )
    sample_analyze.analyze_track(str(wav))
    assert "resample" not in calls


def test_warm_dsp_tolerates_missing_librosa(monkeypatch):
    """warm_dsp must never raise: a missing librosa still has to surface as
    the honest per-track ImportError, not a dead warmup thread."""

    def _boom():
        raise ImportError("no librosa here")

    monkeypatch.setattr(sample_analyze, "_load_librosa", _boom)
    sample_analyze.warm_dsp()  # must not raise


def test_warm_dsp_runs_real_pipeline():
    """With librosa present the warmup exercises the real JIT'd path."""
    pytest.importorskip("librosa")
    sample_analyze.warm_dsp()  # must not raise


def test_analyze_quality_at_44100(tmp_path):
    """End-to-end quality on the resample path: 44100 Hz click track at
    100 BPM must still hit the tempo and most onsets."""
    pytest.importorskip("librosa")
    bpm = 100.0
    beat_s = 60.0 / bpm
    seconds = 8.0
    n = int(seconds * SR)
    y = np.zeros(n, dtype=np.float32)
    click = np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR)).astype(np.float32)
    beats = []
    b = 0.0
    while b < seconds - 0.05:
        beats.append(b)
        i = int(b * SR)
        y[i : i + len(click)] += 0.9 * click
        b += beat_s
    y = (y / np.max(np.abs(y)) * 0.9).astype(np.float32)
    wav = tmp_path / "click44.wav"
    sf.write(str(wav), y, SR, subtype="PCM_16")

    result = sample_analyze.analyze_track(str(wav))
    assert abs(result["bpm"] - bpm) / bpm < 0.02, f"bpm={result['bpm']}"
    matched = sum(
        1 for t in beats if any(abs(d - t) <= 0.1 for d in result["onsets"])
    )
    assert matched / len(beats) >= 0.8, f"recall too low: {result['onsets']!r:.80}"
