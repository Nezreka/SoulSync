"""Sample Studio Phase 3 — render_chop unit tests.

Synthesizes deterministic fixtures (no network, no library), then asserts:
slice duration, pitch-shift accuracy (FFT fundamental check, same pattern as
the Phase-1 spike), time-stretch ratio, preview length cap, engine fallback
with and without Rubber Band, format writing, stereo preservation.
"""

import os

import numpy as np
import pytest
import soundfile as sf

from core.sample import render as sample_render

SR = 22050


def _sine_track(path, seconds=4.0, freq=220.0, stereo=True):
    """Pure sine — unambiguous fundamental for the pitch-shift FFT check."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    y = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    if stereo:
        y = np.stack([y, y * 0.8], axis=1)
    sf.write(str(path), y, SR, subtype="PCM_16")
    return path


def _fft_peak(path):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / sr)
    return freqs[int(np.argmax(spec[1:])) + 1]


def test_render_chop_slice_duration(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav")
    out = tmp_path / "chop.wav"
    result = sample_render.render_chop(str(wav), 0.5, 1.5, out_path=str(out))
    assert abs(result["duration_s"] - 1.0) < 0.02
    assert os.path.isfile(str(out))


def test_render_chop_pitch_shift_moves_fundamental(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav", seconds=2.0, freq=220.0)
    out = tmp_path / "shifted.wav"
    result = sample_render.render_chop(str(wav), 0, 2.0, pitch_st=12,
                                       engine="preview", out_path=str(out))
    peak = _fft_peak(str(out))
    # +12 semitones: 220 Hz -> 440 Hz, allow 2% (phase-vocoder is exact-ish on sines)
    assert abs(peak - 440.0) / 440.0 < 0.02, f"fft peak={peak}"
    assert result["engine"] == "librosa"


def test_render_chop_time_stretch_halves_duration(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav")
    out = tmp_path / "stretched.wav"
    result = sample_render.render_chop(
        str(wav), 0, 4.0, target_bpm=200, source_bpm=100, out_path=str(out)
    )
    assert abs(result["duration_s"] - 2.0) < 0.05


def test_render_chop_needs_source_bpm_for_stretch(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav")
    with pytest.raises(ValueError, match="BPM is unknown"):
        sample_render.render_chop(str(wav), 0, 1.0, target_bpm=120, source_bpm=None,
                                  out_path=str(tmp_path / "x.wav"))


def test_render_chop_preview_cap(tmp_path):
    wav = _sine_track(tmp_path / "long.wav", seconds=70.0)
    with pytest.raises(ValueError, match="too long"):
        sample_render.render_chop(str(wav), 0, 61.0, preview=True,
                                  out_path=str(tmp_path / "x.wav"))
    # Same slice is fine as a final render (under the 600s chop cap).
    result = sample_render.render_chop(str(wav), 0, 61.0, preview=False,
                                       out_path=str(tmp_path / "ok.wav"))
    assert abs(result["duration_s"] - 61.0) < 0.1


def test_render_chop_rejects_bad_input(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav")
    with pytest.raises(ValueError, match="empty slice"):
        sample_render.render_chop(str(wav), 2.0, 1.0, out_path=str(tmp_path / "x.wav"))
    with pytest.raises(ValueError, match="unknown format"):
        sample_render.render_chop(str(wav), 0, 1.0, out_format="mp3",
                                  out_path=str(tmp_path / "x.wav"))


def test_render_chop_flac_and_stereo(tmp_path):
    wav = _sine_track(tmp_path / "sine.wav", stereo=True)
    out = tmp_path / "chop.flac"
    result = sample_render.render_chop(str(wav), 0, 1.0, out_path=str(out), out_format="flac")
    assert result["format"] == "flac"
    data, _ = sf.read(str(out), always_2d=True)
    assert data.shape[1] == 2, "stereo must survive the render"
    assert abs(len(data) / SR - 1.0) < 0.02


def test_select_engine_falls_back_without_rubberband(monkeypatch):
    monkeypatch.setattr(sample_render, "_rubberband_cli_available", lambda: False)
    monkeypatch.setattr(sample_render, "_pyrubberband_available", lambda: False)
    assert sample_render.select_engine("auto") == "librosa"
    assert sample_render.select_engine("final") == "librosa"
    assert sample_render.select_engine("rubberband") == "librosa"
    assert sample_render.select_engine("preview") == "librosa"


def test_select_engine_prefers_installed_rubberband(monkeypatch):
    monkeypatch.setattr(sample_render, "_rubberband_cli_available", lambda: True)
    assert sample_render.select_engine("final") == "rubberband"
    assert sample_render.select_engine("preview") == "librosa"


def test_rubberband_plain_slice_preserves_audio_without_cli_error(tmp_path, monkeypatch):
    # The CLI rejects a render with no pitch/tempo options. An unchanged slice
    # must keep its samples and avoid launching that invalid command.
    audio = np.linspace(-0.5, 0.5, 1024, dtype=np.float32)[:, None]

    def unexpected_command(*args, **kwargs):
        pytest.fail("an unchanged slice must not invoke rubberband")

    monkeypatch.setattr(sample_render.subprocess, "run", unexpected_command)
    rendered, sr = sample_render._apply_rubberband_cli(audio, SR, 0, 1, str(tmp_path))
    assert sr == SR
    np.testing.assert_array_equal(rendered, audio)
