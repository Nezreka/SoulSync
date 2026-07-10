"""Regression: the Lossy Converter fix action surfaces the REAL ffmpeg error in
the finding result shown in the UI notification (issue #995), not ffmpeg's
version banner.

Before the fix, ``_fix_missing_lossy_copy`` returned ``proc.stderr[:200]`` — the
leading banner — so every failed conversion notification read as an identical
"ffmpeg version 7.x ... configuration: ..." blob with no actionable reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from core.repair_worker import RepairWorker


# Faithful ffmpeg 7.x failure: banner first, real reason (missing libopus) last.
BANNER_MISSING_ENCODER = (
    "ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers\n"
    "  built with gcc 14 (Debian 14.2.0-19)\n"
    "  configuration: --prefix=/usr --enable-gpl --enable-libmp3lame\n"
    "  libavutil      59.  8.100 / 59.  8.100\n"
    "Input #0, flac, from '/music/track.flac':\n"
    "  Stream #0:0: Audio: flac, 44100 Hz, stereo, s16\n"
    "[aost#0:0 @ 0x55d0aa] Unknown encoder 'libopus'\n"
    "[aost#0:0 @ 0x55d0aa] Error selecting an encoder\n"
    "Error opening output file /music/track.opus.\n"
    "Error opening output files: Encoder not found\n"
)


def _worker(tmp_path: Path) -> RepairWorker:
    worker = RepairWorker(database=SimpleNamespace())
    cfg = SimpleNamespace()
    values = {
        "lossy_copy.codec": "opus",
        "lossy_copy.bitrate": "256",
        "soulseek.download_path": "",
        "repair.jobs.lossy_converter.settings": {},
    }
    cfg.get = lambda key, default=None: values.get(key, default)
    worker._config_manager = cfg
    worker.transfer_folder = str(tmp_path)
    return worker


def test_fix_surfaces_real_ffmpeg_error_not_banner(tmp_path, monkeypatch):
    flac = tmp_path / "01 - Track.flac"
    flac.write_bytes(b"x")

    monkeypatch.setattr(shutil, "which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr=BANNER_MISSING_ENCODER, stdout=""),
    )

    result = _worker(tmp_path)._fix_missing_lossy_copy("track", "1", str(flac), {})

    assert result["success"] is False
    err = result["error"]
    # Actionable reason present...
    assert "Unknown encoder 'libopus'" in err
    assert "Encoder not found" in err
    # ...banner absent.
    assert "ffmpeg version" not in err
    assert "configuration:" not in err
