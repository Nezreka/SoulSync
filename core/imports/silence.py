"""Silence guard — detect files whose container duration looks right but whose
audio is mostly silence.

Motivating bug: HiFi/Monochrome HLS assembly can yield a file with the full
track duration in its container while only the first ~30s carry real audio and
the rest is silence. The duration-agreement and quality guards both pass (the
container says 3:30 and the format/bitrate are fine), so nothing catches it —
until you listen. This guard runs ffmpeg ``silencedetect`` over the real audio
and flags a file whose silent fraction exceeds a threshold.

The parser (``silence_ratio_from_output``) is pure and unit-tested; the ffmpeg
invocation is integration glue that fails open (returns None) when ffmpeg or
mutagen can't run, so it never blocks a legitimate import on tooling problems.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("imports.silence")

# Defaults: treat audio below -50 dB lasting >= 2s as silence, and reject when
# more than half the track is silent. A normal song — even with quiet intros/
# outros — sits far below 0.5; a 30s-real + padded-silence file sits near 0.85.
DEFAULT_NOISE_DB = -50
DEFAULT_MIN_SILENCE_S = 2.0
DEFAULT_THRESHOLD = 0.5

_SILENCE_DURATION_RE = re.compile(r"silence_duration:\s*([0-9]+(?:\.[0-9]+)?)")


def silence_ratio_from_output(ffmpeg_stderr: str, total_duration_s: float) -> float:
    """Fraction of *total_duration_s* covered by detected silence.

    Sums every ``silence_duration: X`` reported by ffmpeg ``silencedetect``
    and divides by the track length. Capped at 1.0; returns 0.0 when the
    duration is unknown/zero or no silence was reported.
    """
    if not total_duration_s or total_duration_s <= 0:
        return 0.0
    total_silence = sum(float(m) for m in _SILENCE_DURATION_RE.findall(ffmpeg_stderr or ""))
    if total_silence <= 0:
        return 0.0
    return min(total_silence / total_duration_s, 1.0)


def is_mostly_silent_reason(
    ffmpeg_stderr: str,
    total_duration_s: float,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Optional[str]:
    """Return a rejection reason when the silent fraction meets *threshold*."""
    ratio = silence_ratio_from_output(ffmpeg_stderr, total_duration_s)
    if ratio >= threshold:
        pct = round(ratio * 100)
        audible_s = round(total_duration_s * (1 - ratio))
        return (
            f"Audio is mostly silent: {pct}% silence (only ~{audible_s}s audible of "
            f"{round(total_duration_s)}s) — likely a truncated/preview file padded "
            f"to full length"
        )
    return None


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=True,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _probe_duration_s(file_path: str) -> Optional[float]:
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if audio and audio.info and getattr(audio.info, "length", None):
            return float(audio.info.length)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("silence guard duration probe failed for %s: %s", file_path, exc)
    return None


def detect_mostly_silent(
    file_path: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    noise_db: int = DEFAULT_NOISE_DB,
    min_silence_s: float = DEFAULT_MIN_SILENCE_S,
) -> Optional[str]:
    """Run ffmpeg ``silencedetect`` over *file_path* and return a rejection
    reason when the file is mostly silence, else None.

    Fails open: returns None when ffmpeg/mutagen are unavailable or error, so
    a tooling problem never quarantines a legitimate file.
    """
    if not _ffmpeg_available():
        logger.debug("silence guard skipped — ffmpeg not available")
        return None

    total_duration_s = _probe_duration_s(file_path)
    if not total_duration_s:
        return None

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", file_path,
                "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("silence guard ffmpeg run failed for %s: %s", file_path, exc)
        return None

    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    return is_mostly_silent_reason(stderr, total_duration_s, threshold=threshold)
