"""Opt-in auto-sync of downloaded subtitles via ffsubsync.

ffsubsync (pip, offline, no API key) shifts a subtitle's timings to match the
video's own audio. Viability verified Oct 2026 against ffsubsync 0.5.1:
pip-installable, fully offline (local VAD + alignment, no network), runs
end-to-end on real media with exit 0, accepts ``--ffmpeg-path`` (so it can
use SoulSync's tools/ copy), and ``--skip-sync-on-low-quality`` leaves
subtitles unmodified when the alignment looks untrustworthy instead of
applying a probably-wrong shift.

Design: best-effort wrapper. ``sync_subtitle_text()`` returns the synced SRT
or None — callers keep the original text on None. Never raises. Only the
subtitle worker calls it (background thread); the import hook never runs it.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

#: Bound the CPU work: ffsubsync is VAD + alignment (much lighter than
#: transcription), but a pathological file must never wedge the worker.
DEFAULT_TIMEOUT_S = 600


def ffsubsync_bin() -> str | None:
    """The ffsubsync console script (pip installs it next to the package).
    Never raises."""
    try:
        return shutil.which("ffsubsync")
    except Exception:  # noqa: BLE001
        return None


def ffsubsync_available() -> bool:
    """True when ffsubsync can actually run here. Never raises."""
    try:
        return ffsubsync_bin() is not None
    except Exception:  # noqa: BLE001
        return False


def _resolve_ffmpeg() -> str | None:
    """ffmpeg on PATH, else the copy SoulSync keeps in tools/ (Sample Studio
    pattern). ffsubsync shells to ffmpeg for audio extraction; without this
    it would fail on installs where ffmpeg only lives in tools/."""
    try:
        found = shutil.which("ffmpeg")
        if found:
            return found
        from pathlib import Path
        tools = Path(__file__).resolve().parents[3] / "tools"
        names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
        for name in names:
            cand = tools / name
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return None


def sync_subtitle_text(srt_text, video_path, timeout_s: int = DEFAULT_TIMEOUT_S,
                       ) -> str | None:
    """Shift ``srt_text``'s timings to ``video_path``'s audio via ffsubsync.

    Returns the synced SRT, or None when syncing isn't possible — ffsubsync
    missing, bad inputs, timeout, nonzero exit, or empty output. Callers keep
    the original text on None: a sync failure must never fail the download.
    Never raises.
    """
    try:
        text = str(srt_text or "")
        vp = str(video_path or "")
        if not text.strip() or not vp or not os.path.isfile(vp):
            return None
        binary = ffsubsync_bin()
        if not binary:
            return None
        try:
            timeout = max(1, int(timeout_s))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        with tempfile.TemporaryDirectory(prefix="soulsync-autosync-") as tmp:
            src = os.path.join(tmp, "input.srt")
            dst = os.path.join(tmp, "synced.srt")
            with open(src, "w", encoding="utf-8") as f:
                f.write(text)
            cmd = [binary, vp, "-i", src, "-o", dst,
                   "--skip-sync-on-low-quality"]
            ffmpeg = _resolve_ffmpeg()
            if ffmpeg:
                cmd += ["--ffmpeg-path", ffmpeg]
            try:
                proc = subprocess.run(cmd, capture_output=True,
                                      timeout=timeout)
            except (OSError, subprocess.SubprocessError):
                return None
            if proc.returncode != 0 or not os.path.isfile(dst):
                return None
            with open(dst, "r", encoding="utf-8", errors="replace") as f:
                out = f.read()
            return out if out and out.strip() else None
    except Exception:  # noqa: BLE001 - sync is best-effort, never fatal
        return None


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ffsubsync_bin",
    "ffsubsync_available",
    "sync_subtitle_text",
]
