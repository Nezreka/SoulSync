"""a lossless file that was made from a lossy one.

a FLAC cut from a 128 kbps MP3 is a FLAC: it parses, it has the right length,
it "meets" a lossless quality target, and it replaced a real MP3 320 as an
upgrade. nothing at import looked inside it.

what a lossy encoder leaves behind is a wall. below about 16 kHz the music
is all there; above it there is nothing at all, where a real recording, even
a dull one, fades out. measured on test files (Nov 25 2026, a band at
13-15.5 kHz against everything above 19.5 kHz, in dB):

    genuine, bright          13-15.5k -53    above 19.5k -44    cliff  -9
    genuine, dull            13-15.5k -68    above 19.5k -59    cliff  -9
    genuine, tape-like       13-15.5k -56    above 19.5k -78    cliff  22
    from mp3 96 kbps         13-15.5k -54    above 19.5k -91    cliff  37
    from mp3 128 kbps        13-15.5k -53    above 19.5k -91    cliff  38
    from aac 128 kbps        13-15.5k -53    above 19.5k -91    cliff  38
    from mp3 192 kbps        13-15.5k -53    above 19.5k -79    cliff  26
    from mp3 320 kbps        13-15.5k -53    above 19.5k -57    cliff   4

so the rule is deliberately the certain end of it: real content at 13-15.5
kHz, nothing above 19.5 kHz, and at least 32 dB between them. that is the
128-kbps class of fake. a 192 sits beside a tape transfer and a 320 beside
any soft master, and a guard that throws away genuine lossless files is
worse than one that lets a good-sounding fake through, so those pass.

opt-in: this runs only where the profile's deep audio verify is on (it
decodes part of the file with ffmpeg). fails open: no ffmpeg, an unreadable
file or a quiet passage is "can't tell", never "fake".
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("imports.lossless_check")

LOSSLESS_EXTENSIONS = (".flac", ".wav", ".aiff", ".aif", ".ape", ".wv")
MID_BAND = (13000, 15500)
TOP_FROM = 19500
# the mid band must hold real content (not a recording that is simply dull
# from the top down), the top must be empty, and the drop between them steep
MID_FLOOR_DB = -72.0
TOP_CEILING_DB = -85.0
CLIFF_DB = 32.0
SAMPLE_SECONDS = 30
_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def judge(mid_db: Optional[float], top_db: Optional[float]) -> Optional[str]:
    """the verdict on two measured levels: a reason when the file is a
    lossy transcode, None when it isn't or can't be told. pure."""
    if mid_db is None or top_db is None:
        return None
    if mid_db < MID_FLOOR_DB:
        return None                      # nothing up there to fall from: dull, not fake
    if top_db > TOP_CEILING_DB or (mid_db - top_db) < CLIFF_DB:
        return None
    return ("lossless file made from a lossy source: music up to "
            f"{MID_BAND[1] / 1000:g} kHz ({mid_db:.0f} dB) and nothing above "
            f"{TOP_FROM / 1000:g} kHz ({top_db:.0f} dB), the cut an MP3 or AAC "
            "encoder makes")


def _steep(kind: str, hz: int) -> str:
    # one filter rolls off too gently and the loud low end leaks through it
    return ",".join(f"{kind}=f={hz}" for _ in range(4))


def _band_level(path: str, start: float, filters: str) -> Optional[float]:
    command = ["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-ss", f"{start:.1f}",
               "-t", str(SAMPLE_SECONDS), "-i", path, "-af", f"{filters},volumedetect",
               "-f", "null", "-"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("band level failed for %s: %s", os.path.basename(path), exc)
        return None
    found = _MEAN.findall(done.stderr or "")
    return float(found[-1]) if found else None


def detect_lossy_transcode(file_path: str) -> Optional[str]:
    """a reason when ``file_path`` is a lossless file cut from a lossy one,
    else None. measures thirty seconds from a third of the way in (an intro
    is often quiet)."""
    if os.path.splitext(str(file_path))[1].lower() not in LOSSLESS_EXTENSIONS:
        return None
    try:
        from core.imports.silence import _ffmpeg_available
        if not _ffmpeg_available():
            return None
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        length = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
        sample_rate = int(getattr(getattr(audio, "info", None), "sample_rate", 0) or 0)
    except Exception as exc:  # noqa: BLE001 - can't tell is not fake
        logger.debug("lossless check skipped for %s: %s", file_path, exc)
        return None
    # the top band needs room under the file's own limit, and a few seconds
    # of audio to measure
    if sample_rate < 44100 or length < 8:
        return None
    start = max(0.0, min(length * 0.35, length - SAMPLE_SECONDS))
    mid = _band_level(file_path, start, _steep("highpass", MID_BAND[0]) + "," + _steep("lowpass", MID_BAND[1]))
    if mid is None or mid < MID_FLOOR_DB:
        return None
    return judge(mid, _band_level(file_path, start, _steep("highpass", TOP_FROM)))


__all__ = ["detect_lossy_transcode", "judge", "LOSSLESS_EXTENSIONS"]
