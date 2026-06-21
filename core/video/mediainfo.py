"""Probe a finished video file with ffprobe for its TRUE media info.

We otherwise trust the release NAME for resolution/quality, and names lie: 720p
upscales labelled 1080p, trailers/samples labelled as the feature, broken muxes.
ffprobe reads the real container — duration, dimensions → resolution, codecs — so the
importer can tag the file by its actual quality and reject corrupt / too-short junk.

The parsing (``parse_ffprobe`` / ``resolution_from_dimensions``) is pure and unit-tested
on canned JSON; the subprocess runner is injected, so nothing here needs ffmpeg to be
tested. ffmpeg is OPTIONAL — when ffprobe isn't installed, or it errors, ``probe``
returns None and the caller falls back to the scene name.

Three outcomes, deliberately distinct:
  - None              → couldn't verify (ffprobe missing / crashed / timed out) → skip
  - {"ok": False, …}  → ffprobe ran and found NO video stream → corrupt / fake
  - {"ok": True,  …}  → real media info to trust over the name

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

_FFPROBE = "ffprobe"


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def resolution_from_dimensions(width: Any, height: Any) -> str | None:
    """Bucket real pixel dimensions into a resolution label. Uses the LARGER axis so
    a letterboxed 1920x800 movie reads as 1080p (not 720p by its short side)."""
    ref = max(_int(width), _int(height))
    if ref <= 0:
        return None
    if ref >= 3000:
        return "2160p"
    if ref >= 1700:
        return "1080p"
    if ref >= 1100:
        return "720p"
    return "480p"


def _norm_codec(name: Any) -> str | None:
    s = str(name or "").strip().lower()
    if not s:
        return None
    if s in ("hevc", "h265", "x265"):
        return "hevc"
    if s in ("h264", "avc", "x264"):
        return "x264"
    if s == "av1":
        return "av1"
    return s


def parse_ffprobe(data: Any) -> dict:
    """Parse ffprobe's ``-show_format -show_streams`` JSON into the fields we use.
    ``ok`` is True only when a video stream is present (else: corrupt / not a video)."""
    data = data if isinstance(data, dict) else {}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _float(fmt.get("duration")) or _float((video or {}).get("duration"))
    width = (video or {}).get("width")
    height = (video or {}).get("height")
    return {
        "ok": video is not None,
        "duration_sec": duration,
        "width": _int(width),
        "height": _int(height),
        "resolution": resolution_from_dimensions(width, height) if video else None,
        "video_codec": _norm_codec((video or {}).get("codec_name")),
        "audio_codec": str((audio or {}).get("codec_name") or "") or None,
    }


def ffprobe_available() -> bool:
    return shutil.which(_FFPROBE) is not None


def _default_runner(path: str) -> str | None:
    """Run ffprobe and return its JSON stdout, or None on any failure (so a transient
    ffprobe error degrades to 'unverified', never to a false 'corrupt')."""
    try:
        proc = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except Exception:   # noqa: BLE001 - missing binary / timeout / OS error → unverified
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    return proc.stdout


def probe(path: Any, runner: Callable | None = None) -> dict | None:
    """Probe ``path`` and return parsed media info, or None when it can't be verified.
    ``runner(path)->json_str|None`` is injected (real ffprobe in prod, canned in tests).
    When no runner is given and ffprobe isn't installed, returns None (skip verify)."""
    use = runner if runner is not None else (_default_runner if ffprobe_available() else None)
    if use is None:
        return None
    try:
        raw = use(path)
    except Exception:   # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parse_ffprobe(data)


__all__ = [
    "resolution_from_dimensions", "parse_ffprobe", "ffprobe_available", "probe",
]
