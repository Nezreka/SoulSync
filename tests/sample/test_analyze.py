"""Sample Studio Phase 1 — analyze_track / compute_peaks / store round-trip.

Synthesizes a deterministic click track (no fixtures, no network), then asserts
the DSP finds the right tempo and most of the onsets. Mirrors the Phase-1 spike
(~/workspace/sampler-spike/make_track.py) in miniature.
"""

import numpy as np
import pytest
import soundfile as sf

from core.sample import analyze as sample_analyze
from core.sample import store as sample_store

SR = 22050
BPM = 100.0
BEAT_S = 60.0 / BPM


def _click_track(path, seconds=8.0):
    """Click on every beat + a soft off-beat tick, over a low hum."""
    n = int(seconds * SR)
    y = np.zeros(n, dtype=np.float32)
    # low hum so the signal isn't pure silence between clicks
    t = np.arange(n) / SR
    y += 0.05 * np.sin(2 * np.pi * 55 * t).astype(np.float32)
    click = np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR)).astype(np.float32)
    beats = []
    b = 0.0
    while b < seconds - 0.05:
        beats.append(b)
        i = int(b * SR)
        y[i : i + len(click)] += 0.9 * click
        # off-beat tick, quieter
        j = int((b + BEAT_S / 2) * SR)
        if j + len(click) < n:
            y[j : j + len(click)] += 0.3 * click
        b += BEAT_S
    peak = np.max(np.abs(y))
    y = (y / peak * 0.9).astype(np.float32)
    sf.write(str(path), y, SR, subtype="PCM_16")
    return beats


def _recall(detected, truth, tol=0.1):
    matched = 0
    for t in truth:
        if any(abs(d - t) <= tol for d in detected):
            matched += 1
    return matched / max(1, len(truth))


def test_analyze_track_finds_tempo_and_onsets(tmp_path):
    wav = tmp_path / "click.wav"
    beats = _click_track(wav)
    result = sample_analyze.analyze_track(str(wav))
    assert result["analyzer_version"] == sample_analyze.ANALYZER_VERSION
    # BPM within 2%
    assert abs(result["bpm"] - BPM) / BPM < 0.02, f"bpm={result['bpm']}"
    # onset recall sane (tolerance 100ms against beat times)
    assert _recall(result["onsets"], beats) >= 0.8, f"recall too low: {len(result['onsets'])} onsets vs {len(beats)} beats"
    assert abs(result["duration_s"] - 8.0) < 0.05


def test_analyze_track_missing_file():
    with pytest.raises(FileNotFoundError):
        sample_analyze.analyze_track("/nonexistent/track.wav")


def test_compute_peaks_shape(tmp_path):
    wav = tmp_path / "click.wav"
    _click_track(wav)
    peaks = sample_analyze.compute_peaks(str(wav), buckets=1500)
    assert peaks["buckets"] == 1500
    assert len(peaks["min"]) == 1500
    assert len(peaks["max"]) == 1500
    assert all(lo <= hi for lo, hi in zip(peaks["min"], peaks["max"]))
    assert abs(peaks["duration_s"] - 8.0) < 0.05


def test_compute_peaks_rejects_bad_buckets(tmp_path):
    wav = tmp_path / "click.wav"
    _click_track(wav)
    with pytest.raises(ValueError):
        sample_analyze.compute_peaks(str(wav), buckets=4)


def test_store_round_trip():
    # conftest redirects DATABASE_PATH to a throwaway temp DB; the
    # sample_analysis table is created by MusicDatabase._initialize_database.
    track_id = 424242
    assert sample_store.get_analysis(track_id) is None
    assert not sample_store.is_current(track_id)
    sample_store.save_analysis(
        track_id,
        {
            "bpm": 100.0,
            "onsets": [0.6, 1.2, 1.8],
            "duration_s": 8.0,
            "analyzer_version": sample_analyze.ANALYZER_VERSION,
        },
    )
    row = sample_store.get_analysis(track_id)
    assert row["bpm"] == 100.0
    assert row["onsets"] == [0.6, 1.2, 1.8]
    assert sample_store.is_current(track_id)


def test_decode_mono_lossy_fallback(tmp_path):
    # soundfile can't read this; the ffmpeg fallback must kick in.
    pytest.importorskip("numpy")
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    wav = tmp_path / "click.wav"
    _click_track(wav, seconds=2.0)
    mp3 = tmp_path / "click.mp3"
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
    mono, sr = sample_analyze.decode_mono(str(mp3))
    assert sr == 44100
    assert abs(len(mono) / sr - 2.0) < 0.15
