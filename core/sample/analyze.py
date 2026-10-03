"""Sample Studio — server-side audio analysis (Phase 1).

Decode + onset/chop detection + BPM + waveform peaks for the sampler page.
librosa/soundfile are imported LAZILY inside the functions below so importing
this module (and therefore web_server.py) never pays the numba/JIT import cost
or hard-fails when the optional DSP deps are missing — callers get a clean
ImportError only when they actually try to analyze.
"""

from __future__ import annotations

import io
import os
import subprocess
from typing import Any, Dict, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("sample.analyze")

# Bump when the analysis algorithm changes; the worker skips tracks already
# analyzed at the current version.
# v3: adds the musical key.
ANALYZER_VERSION = 3

# Analysis runs at 22050 Hz (librosa's canonical rate): ~4x fewer samples
# through the STFT than 44.1k source audio, with no measurable BPM loss
# (99.4 vs 99.4 on the real 100-BPM spike fixture; <=1.6% on synthetic).
_ANALYSIS_SR = 22050

# soundfile cannot decode these — go straight to ffmpeg for them.
_LOSSY_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wma"}

# Tuned on the Phase-1 spike fixture (12s @100 BPM, 40 ground-truth onsets):
# precision 0.951 / recall 0.975 vs 0.736 / 0.975 for librosa defaults.
# See ~/workspace/sampler-spike/SPIKE_REPORT.md.
# v2: windows rescaled to the analysis frame rate (hop 512 @22050 Hz =
# 23.2ms/frame vs 11.6ms @44100) to preserve the tuned ~time constants
# (pre_max ~60ms, pre_avg ~350ms, wait ~90ms).
_PEAK_PICK_KWARGS = dict(pre_max=3, post_max=3, pre_avg=15, post_avg=15, delta=0.25, wait=4)


def _load_librosa():
    try:
        import librosa  # noqa: F401
        import librosa as _lr

        return _lr
    except ImportError as exc:
        raise ImportError("librosa is required for sample analysis (pip install librosa)") from exc


def _load_soundfile():
    try:
        import soundfile as sf

        return sf
    except ImportError as exc:
        raise ImportError("soundfile is required for sample analysis (pip install soundfile)") from exc


def _decode_via_ffmpeg(file_path: str, target_sr: int = 44100) -> Tuple[Any, int]:
    """Decode any format ffmpeg understands to mono float32 via a wav pipe.

    The timeout is load-bearing: the analysis worker is a single FIFO thread,
    so a hung ffmpeg would wedge analysis for every track behind it. A
    timeout surfaces as a normal worker error (sticky status + Try again).
    """
    sf = _load_soundfile()
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", file_path, "-ac", "1", "-ar", str(target_sr), "-f", "wav", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out decoding {file_path}") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg could not decode {file_path}: {proc.stderr.decode(errors='replace')[:300]}")
    data, sr = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=False)
    return data, sr


def decode_mono(file_path: str) -> Tuple[Any, int]:
    """Decode an audio file to mono float32.

    soundfile handles WAV/FLAC/AIFF; anything else (or any soundfile failure)
    falls back to ffmpeg, which is already a SoulSync dependency.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"audio file not found: {file_path}")
    sf = _load_soundfile()
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _LOSSY_EXTS:
        return _decode_via_ffmpeg(file_path)
    try:
        data, sr = sf.read(file_path, dtype="float32", always_2d=True)
    except Exception:
        logger.info("soundfile could not read %s — falling back to ffmpeg", file_path)
        return _decode_via_ffmpeg(file_path)
    mono = data.mean(axis=1).astype("float32")
    return mono, sr


def analyze_track(file_path: str) -> Dict[str, Any]:
    """Full analysis for one track: BPM, onset times, duration.

    Returns {"bpm": float, "onsets": [seconds...], "duration_s": float,
             "key": {"name", "confidence"} | None, "analyzer_version": int}.

    Performance: the audio is downsampled to 22050 Hz once, and the onset
    envelope is computed ONCE and shared with beat tracking (beat_track's
    internal onset_strength used to double the STFT cost). Measured on a
    4:34 track: 6.9s -> 0.8s with no BPM loss on real audio.
    """
    librosa = _load_librosa()
    import numpy as np

    mono, sr = decode_mono(file_path)
    duration_s = float(len(mono) / sr)

    y = (
        librosa.resample(mono, orig_sr=sr, target_sr=_ANALYSIS_SR)
        if sr != _ANALYSIS_SR
        else mono
    )
    onset_envelope = librosa.onset.onset_strength(y=y, sr=_ANALYSIS_SR)
    peak_frames = librosa.util.peak_pick(onset_envelope, **_PEAK_PICK_KWARGS)
    onsets = [
        round(float(t), 3)
        for t in librosa.frames_to_time(peak_frames, sr=_ANALYSIS_SR)
    ]

    tempo_raw, _ = librosa.beat.beat_track(
        onset_envelope=onset_envelope, sr=_ANALYSIS_SR
    )
    bpm = round(float(np.atleast_1d(tempo_raw)[0]), 1)

    from .key import detect_key

    return {
        "bpm": bpm,
        "onsets": onsets,
        "duration_s": round(duration_s, 3),
        "key": detect_key(y, _ANALYSIS_SR),
        "analyzer_version": ANALYZER_VERSION,
    }


def warm_dsp() -> None:
    """Pay the librosa import + numba JIT cost off the critical path.

    Fire-and-forget from the worker at boot: the first real track then skips
    the cold-start stall (import + JIT can dwarf the DSP itself on slow
    machines). Never raises — a missing librosa must still surface as the
    honest per-track ImportError, not a dead warmup thread.
    """
    try:
        librosa = _load_librosa()
        import numpy as np

        y = np.zeros(_ANALYSIS_SR, dtype=np.float32)
        y[::_ANALYSIS_SR // 10] = 0.5  # 10 Hz clicks: non-degenerate envelope
        env = librosa.onset.onset_strength(y=y, sr=_ANALYSIS_SR)
        librosa.beat.beat_track(onset_envelope=env, sr=_ANALYSIS_SR)
    except Exception:
        logger.debug("DSP warmup skipped", exc_info=True)


def compute_peaks(file_path: str, buckets: int = 1500) -> Dict[str, Any]:
    """Min/max waveform peaks for the editor canvas.

    Returns {"buckets": n, "duration_s": float, "min": [...], "max": [...]}.
    ~0.1s for a 4-minute track — cheap enough to compute on demand and cache.
    """
    import numpy as np

    if buckets < 16 or buckets > 20000:
        raise ValueError(f"buckets must be 16..20000, got {buckets}")
    mono, sr = decode_mono(file_path)
    duration_s = float(len(mono) / sr)

    # Pad so the samples divide evenly, then one vectorized min/max.
    padded_len = int(np.ceil(len(mono) / buckets) * buckets)
    padded = np.pad(mono, (0, padded_len - len(mono)))
    reshaped = padded.reshape(buckets, -1)
    lo = reshaped.min(axis=1).astype(float).tolist()
    hi = reshaped.max(axis=1).astype(float).tolist()
    return {"buckets": buckets, "duration_s": round(duration_s, 3), "min": lo, "max": hi}


def onsets_list(result: Dict[str, Any]) -> List[float]:
    """Convenience accessor for the onset list in an analyze_track() result."""
    return [float(t) for t in result.get("onsets", [])]
