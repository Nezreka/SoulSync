"""Sample Studio — chop rendering (Phase 3).

Slice + pitch-shift + time-stretch for previews and saved chops.

Engine selection
----------------
* ``"preview"`` — librosa phase-vocoder path. Fast (<1s of DSP for
  seconds-long chops, spike-verified) but audibly smears drum transients.
* ``"final"`` / ``"auto"`` — Rubber Band when available (``rubberband`` CLI
  first, then the ``pyrubberband`` package), falling back to librosa with a
  logged warning. Rubber Band preserves transients materially better, so it
  is the render path for *saved* chops.
* Explicit ``"rubberband"`` / ``"librosa"`` also accepted.

Rubber Band is NOT installed in this dev environment (no CLI, no package),
so every path here is exercised against the librosa fallback in tests. The
SoulSync Docker image needs the ``rubberband-cli`` apt package for the
quality tier — that is the whole install, do not build C++ from source.

Unlike analyze.py (mono), rendering keeps the source channel layout so
saved chops are proper stereo files.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("sample.render")

RENDERER_VERSION = 1

# Previews are interactive (debounced slider drags): refuse anything longer.
MAX_PREVIEW_SECONDS = 60.0
# Final renders run synchronously in the request; bound them too so one
# pathological save can't hang a worker for an hour.
MAX_CHOP_SECONDS = 600.0

_FORMATS = {
    "wav16": {"subtype": "PCM_16", "ext": ".wav", "format": "WAV"},
    "wav24": {"subtype": "PCM_24", "ext": ".wav", "format": "WAV"},
    "flac": {"subtype": "PCM_24", "ext": ".flac", "format": "FLAC"},
}


def _load_soundfile():
    try:
        import soundfile as sf

        return sf
    except ImportError as exc:
        raise ImportError("soundfile is required for sample rendering (pip install soundfile)") from exc


def _load_librosa():
    try:
        import librosa as _lr

        return _lr
    except ImportError as exc:
        raise ImportError("librosa is required for sample rendering (pip install librosa)") from exc


def decode_stereo(file_path: str) -> Tuple[Any, int]:
    """Decode an audio file to stereo float32 (n_samples, n_channels).

    The source sample rate is preserved — no resampling, no fidelity loss.
    soundfile handles WAV/FLAC/AIFF; anything else (or any soundfile failure)
    falls back to ffmpeg, which is already a SoulSync dependency.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"audio file not found: {file_path}")
    sf = _load_soundfile()
    try:
        return sf.read(file_path, dtype="float32", always_2d=True)
    except Exception:
        logger.info("soundfile could not read %s — falling back to ffmpeg", file_path)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", file_path, "-f", "wav", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg could not decode {file_path}: {proc.stderr.decode(errors='replace')[:300]}")
    return sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)


def _rubberband_cli_available() -> bool:
    return shutil.which("rubberband") is not None


def _pyrubberband_available() -> bool:
    try:
        import pyrubberband  # noqa: F401

        return True
    except ImportError:
        return False


def select_engine(requested: str = "auto") -> str:
    """Resolve an engine name to the concrete engine to use.

    Returns "rubberband" or "librosa". Never raises — worst case is the
    librosa fallback with a logged warning.
    """
    req = (requested or "auto").lower()
    if req in ("preview", "librosa"):
        return "librosa"
    if req == "pyrubberband":
        return "rubberband" if _pyrubberband_available() else "librosa"
    # "auto", "final", "rubberband": prefer Rubber Band, fall back gracefully.
    if _rubberband_cli_available() or _pyrubberband_available():
        return "rubberband"
    if req == "rubberband":
        logger.warning(
            "Rubber Band requested but not installed — falling back to librosa. "
            "Install rubberband-cli in the Docker image for the quality render path."
        )
    return "librosa"


def _apply_librosa(y: Any, sr: int, pitch_st: float, tempo_ratio: float) -> Any:
    """Pitch-shift then time-stretch, per channel. y: (n, channels)."""
    import numpy as np

    librosa = _load_librosa()
    out = y
    if abs(pitch_st) >= 0.01:
        out = np.stack(
            [librosa.effects.pitch_shift(ch, sr=sr, n_steps=float(pitch_st)) for ch in out.T],
            axis=1,
        ).astype("float32")
    if abs(tempo_ratio - 1.0) > 1e-3:
        out = np.stack(
            [librosa.effects.time_stretch(ch, rate=float(tempo_ratio)) for ch in out.T],
            axis=1,
        ).astype("float32")
    return out


def _apply_rubberband_cli(y: Any, sr: int, pitch_st: float, tempo_ratio: float, tmpdir: str) -> Tuple[Any, int]:
    """One-pass pitch+tempo via the rubberband CLI. Returns (audio, sr)."""
    import numpy as np

    sf = _load_soundfile()
    in_path = os.path.join(tmpdir, f"rb_in_{uuid.uuid4().hex}.wav")
    out_path = os.path.join(tmpdir, f"rb_out_{uuid.uuid4().hex}.wav")
    sf.write(in_path, y, sr, subtype="FLOAT")
    cmd = ["rubberband", "-q"]
    if abs(pitch_st) >= 0.01:
        cmd += ["--pitch", f"{float(pitch_st):.3f}"]
    if abs(tempo_ratio - 1.0) > 1e-3:
        cmd += ["--tempo", f"{float(tempo_ratio):.4f}"]
    cmd += [in_path, out_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    try:
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace")[:300])
        data, out_sr = sf.read(out_path, dtype="float32", always_2d=True)
        return data, out_sr
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _apply_pyrubberband(y: Any, sr: int, pitch_st: float, tempo_ratio: float) -> Any:
    import numpy as np
    import pyrubberband as pyrb

    out = y
    if abs(pitch_st) >= 0.01:
        out = np.stack(
            [pyrb.pitch_shift(ch, sr, float(pitch_st)) for ch in out.T], axis=1
        ).astype("float32")
    if abs(tempo_ratio - 1.0) > 1e-3:
        out = np.stack(
            [pyrb.time_stretch(ch, sr, float(tempo_ratio)) for ch in out.T], axis=1
        ).astype("float32")
    return out


def render_chop(
    track_path: str,
    start_s: float,
    end_s: float,
    pitch_st: float = 0.0,
    target_bpm: Optional[float] = None,
    source_bpm: Optional[float] = None,
    engine: str = "auto",
    out_path: Optional[str] = None,
    out_format: str = "wav16",
    preview: bool = False,
) -> Dict[str, Any]:
    """Render a chop: slice -> pitch-shift -> time-stretch -> file.

    Returns {"path", "engine", "duration_s", "format", "renderer_version"}.
    Raises ValueError on bad input (400-class), RuntimeError on DSP failure.
    """
    if out_format not in _FORMATS:
        raise ValueError(f"unknown format {out_format!r} (want one of {sorted(_FORMATS)})")
    if not os.path.isfile(track_path):
        raise FileNotFoundError(f"audio file not found: {track_path}")

    y, sr = decode_stereo(track_path)
    duration_s = len(y) / sr
    start_s = max(0.0, float(start_s))
    end_s = min(float(end_s), duration_s)
    if not end_s > start_s:
        raise ValueError(f"empty slice: start={start_s}, end={end_s}, duration={duration_s:.2f}")

    max_len = MAX_PREVIEW_SECONDS if preview else MAX_CHOP_SECONDS
    if end_s - start_s > max_len:
        raise ValueError(
            f"slice too long ({end_s - start_s:.1f}s > {max_len:.0f}s cap) — "
            "narrow the in/out points"
        )

    tempo_ratio = 1.0
    if target_bpm:
        if not source_bpm or source_bpm <= 0:
            raise ValueError("target_bpm given but the track's BPM is unknown — analyze it first")
        tempo_ratio = float(target_bpm) / float(source_bpm)

    s0, s1 = int(start_s * sr), int(end_s * sr)
    clip = y[s0:s1]

    chosen = select_engine(engine)
    tmpdir = tempfile.mkdtemp(prefix="sample_render_")
    try:
        if chosen == "rubberband" and _rubberband_cli_available():
            clip, _ = _apply_rubberband_cli(clip, sr, pitch_st, tempo_ratio, tmpdir)
        elif chosen == "rubberband":
            clip = _apply_pyrubberband(clip, sr, pitch_st, tempo_ratio)
        else:
            clip = _apply_librosa(clip, sr, pitch_st, tempo_ratio)
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    import numpy as np

    peak = float(np.max(np.abs(clip))) if clip.size else 0.0
    if peak > 1.0:
        logger.info("render_chop clipped %.3f -> 1.0 (pitch/tempo can raise level)", peak)
        clip = np.clip(clip, -1.0, 1.0).astype("float32")

    fmt = _FORMATS[out_format]
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="chop_", suffix=fmt["ext"])
        os.close(fd)
    sf = _load_soundfile()
    sf.write(out_path, clip, sr, subtype=fmt["subtype"], format=fmt["format"])

    return {
        "path": out_path,
        "engine": chosen,
        "duration_s": round(float(len(clip) / sr), 3),
        "format": out_format,
        "renderer_version": RENDERER_VERSION,
    }
