"""Audio-completeness guard — detect files whose container duration looks
right but whose REAL audio is far shorter, or mostly silence.

Motivating bug: HiFi/Monochrome HLS assembly can yield a file whose container
claims the full track length (e.g. 3:08) while only ~30s of audio actually
decodes — the rest is missing. The duration-agreement and quality guards both
pass (mutagen reads the container's 3:08 and the format/bitrate are fine), so
nothing catches it until you listen. ffmpeg's ``time=`` even reports 0 with no
error on such a file, so the robust signal is to DECODE the audio and compare
the real duration (sample count / sample rate, via ``astats``) against the
container duration. A separate ``silencedetect`` pass also flags genuine
silence-padding.

The parsers here are pure and unit-tested; the ffmpeg invocations are
integration glue that fails open (returns None) when ffmpeg or mutagen can't
run, so a tooling problem never blocks a legitimate import.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("imports.silence")

# Real decoded audio must cover at least this fraction of the container
# duration. A legit file decodes to ~100% (encoder padding aside); a truncated
# file decodes to a small fraction (the Blossom file: 30s of a 188s container
# = 16%). 0.85 leaves generous headroom against false positives.
DEFAULT_MIN_DURATION_RATIO = 0.85

_SAMPLES_RE = re.compile(r"Number of samples:\s*([0-9]+)")

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


# ── Truncation: real decoded duration vs container duration ────────────────

def measured_duration_from_astats(astats_stderr: str, sample_rate: int) -> Optional[float]:
    """Real decoded audio duration in seconds from ffmpeg ``astats`` output.

    ``astats`` reports the per-channel ``Number of samples``; dividing by the
    sample rate gives the true decoded length. Returns None when the sample
    count or sample rate is unavailable.
    """
    if not sample_rate or sample_rate <= 0:
        return None
    m = _SAMPLES_RE.search(astats_stderr or "")
    if not m:
        return None
    return int(m.group(1)) / float(sample_rate)


def incomplete_audio_reason(
    measured_s: Optional[float],
    container_s: Optional[float],
    *,
    min_ratio: float = DEFAULT_MIN_DURATION_RATIO,
) -> Optional[str]:
    """Return a rejection reason when the real decoded duration falls short of
    the container duration (a truncated file whose metadata over-states length).
    """
    if not measured_s or not container_s or container_s <= 0:
        return None
    if measured_s >= container_s * min_ratio:
        return None
    pct = round(measured_s / container_s * 100)
    return (
        f"Incomplete audio: only ~{round(measured_s)}s actually decodes of a "
        f"{round(container_s)}s file ({pct}%) — truncated/broken download "
        f"(container duration over-states the real audio)"
    )


def _measured_audio_duration_s(file_path: str, sample_rate: int) -> Optional[float]:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", file_path,
             "-af", "astats=metadata=1", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("astats run failed for %s: %s", file_path, exc)
        return None
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    return measured_duration_from_astats(stderr, sample_rate)


def detect_incomplete_audio(
    file_path: str,
    *,
    min_ratio: float = DEFAULT_MIN_DURATION_RATIO,
) -> Optional[str]:
    """Decode the file and reject when the real audio is far shorter than the
    container claims. Fails open when ffmpeg/mutagen are unavailable.
    """
    if not _ffmpeg_available():
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if not (audio and audio.info):
            return None
        container_s = float(getattr(audio.info, "length", 0) or 0)
        sample_rate = int(getattr(audio.info, "sample_rate", 0) or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("container probe failed for %s: %s", file_path, exc)
        return None

    measured_s = _measured_audio_duration_s(file_path, sample_rate)
    return incomplete_audio_reason(measured_s, container_s, min_ratio=min_ratio)


def detect_broken_audio(
    file_path: str,
    *,
    min_ratio: float = DEFAULT_MIN_DURATION_RATIO,
    threshold: float = DEFAULT_THRESHOLD,
    noise_db: int = DEFAULT_NOISE_DB,
    min_silence_s: float = DEFAULT_MIN_SILENCE_S,
) -> Optional[str]:
    """Combined post-download audio guard: reject a file that is truncated
    (real audio far shorter than the container) or mostly silence. Returns the
    first failure reason, or None when the audio looks complete.

    Runs a SINGLE ffmpeg decode pass with both the ``astats`` (truncation) and
    ``silencedetect`` (silence) filters chained — one decode of the file feeds
    both checks instead of two full decodes. Halves the CPU cost versus running
    ``detect_incomplete_audio`` and ``detect_mostly_silent`` back to back.

    Fails open: returns None when ffmpeg/mutagen are unavailable or error, so a
    tooling problem never quarantines a legitimate file.
    """
    if not _ffmpeg_available():
        logger.debug("audio guard skipped — ffmpeg not available")
        return None

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if not (audio and audio.info):
            return None
        container_s = float(getattr(audio.info, "length", 0) or 0)
        sample_rate = int(getattr(audio.info, "sample_rate", 0) or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("container probe failed for %s: %s", file_path, exc)
        return None

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", file_path,
                "-af", f"astats=metadata=1,silencedetect=noise={noise_db}dB:d={min_silence_s}",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("audio guard ffmpeg run failed for %s: %s", file_path, exc)
        return None

    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    # Truncation check first (real audio far shorter than the container).
    measured_s = measured_duration_from_astats(stderr, sample_rate)
    reason = incomplete_audio_reason(measured_s, container_s, min_ratio=min_ratio)
    if reason:
        return reason

    # Then silence-padding (mostly-silent file).
    return is_mostly_silent_reason(stderr, container_s, threshold=threshold)
