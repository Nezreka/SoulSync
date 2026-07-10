"""Regression tests for surfacing the REAL ffmpeg error (issue #995).

The lossy converter (both the Library Maintenance fix action and the
post-download auto-convert path) used to report ``stderr[:200]`` on failure.
ffmpeg prints its version/build banner to stderr on every run and writes the
actual error LAST, so the first 200 chars are always just the banner — every
failure looked identical and unactionable ("conversion fails ... without any
error logs shown").

These fixtures are faithful to real ffmpeg 7.x stderr (captured from ffmpeg
7.0.2; the reporter is on 7.1.5, same shape) so the summarizer is pinned against
the actual output, not a guess.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.imports import file_ops
from core.imports.ffmpeg_errors import summarize_ffmpeg_error


# A missing/unsupported encoder — the most likely real cause on the reporter's
# box (an ffmpeg build without libopus). Banner first, real reason last.
FFMPEG_MISSING_ENCODER = (
    "ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers\n"
    "  built with gcc 14 (Debian 14.2.0-19)\n"
    "  configuration: --prefix=/usr --extra-version=0+deb13u1 --toolchain=hardened "
    "--libdir=/usr/lib/x86_64-linux-gnu --enable-gpl --enable-libmp3lame --enable-libopus\n"
    "  libavutil      59.  8.100 / 59.  8.100\n"
    "  libavcodec     61.  3.100 / 61.  3.100\n"
    "Input #0, flac, from '/music/Bon Jovi/track.flac':\n"
    "  Metadata:\n"
    "    title           : Wanted Dead Or Alive (2003 Acoustic Version)\n"
    "    artist          : Bon Jovi\n"
    "  Duration: 00:04:12.00, start: 0.000000, bitrate: 900 kb/s\n"
    "  Stream #0:0: Audio: flac, 44100 Hz, stereo, s16\n"
    "[aost#0:0 @ 0x55d0aa] Unknown encoder 'libopus'\n"
    "[aost#0:0 @ 0x55d0aa] Error selecting an encoder\n"
    "Error opening output file /music/Bon Jovi/track.opus.\n"
    "Error opening output files: Encoder not found\n"
)

FFMPEG_PERMISSION_DENIED = (
    "ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers\n"
    "  built with gcc 14 (Debian 14.2.0-19)\n"
    "  configuration: --prefix=/usr --enable-libopus\n"
    "  libavutil      59.  8.100 / 59.  8.100\n"
    "Input #0, flac, from '/music/x.flac':\n"
    "  Stream #0:0: Audio: flac, 44100 Hz, stereo, s16\n"
    "[out#0/opus @ 0x2ea] Error opening output /music/x.opus: Permission denied\n"
    "Error opening output file /music/x.opus.\n"
    "Error opening output files: Permission denied\n"
)


class TestSummarizeFfmpegError:
    def test_missing_encoder_surfaces_real_reason_not_banner(self):
        msg = summarize_ffmpeg_error(FFMPEG_MISSING_ENCODER)
        # The actionable root cause is present...
        assert "Unknown encoder 'libopus'" in msg
        assert "Encoder not found" in msg
        # ...and none of the banner noise is.
        assert not msg.startswith("ffmpeg version")
        assert "configuration:" not in msg
        assert "built with" not in msg
        assert "libavutil" not in msg

    def test_permission_denied_surfaces_reason(self):
        msg = summarize_ffmpeg_error(FFMPEG_PERMISSION_DENIED)
        assert "Permission denied" in msg
        assert not msg.startswith("ffmpeg version")

    def test_empty_stderr_gives_actionable_fallback(self):
        assert "ffmpeg" in summarize_ffmpeg_error("").lower()
        assert "ffmpeg" in summarize_ffmpeg_error(None).lower()

    def test_no_error_keyword_falls_back_to_tail_not_banner(self):
        # Only banner + a progress line, no error-shaped line: must still avoid
        # the banner and return the tail.
        stderr = (
            "ffmpeg version 7.1.5 Copyright\n"
            "  configuration: --enable-libopus\n"
            "frame=  100 fps=0.0 q=-1.0 size=    256KiB time=00:00:02.00 bitrate= 274.4kbits/s\n"
        )
        msg = summarize_ffmpeg_error(stderr)
        assert not msg.startswith("ffmpeg version")
        assert "configuration:" not in msg
        assert "frame=" in msg  # the tail line, best available

    def test_generic_conversion_failed_tail_is_captured(self):
        stderr = "ffmpeg version 7.1.5\n  configuration: --x\nConversion failed!\n"
        assert "Conversion failed!" in summarize_ffmpeg_error(stderr)

    def test_respects_max_len(self):
        long_err = "x" * 5000 + "\nError: the real reason at the end\n"
        assert len(summarize_ffmpeg_error(long_err, max_len=120)) <= 120


class TestLossyCopyReportsRealError:
    """Wire-level regression: a failed create_lossy_copy logs the real ffmpeg
    error, not the version banner."""

    @pytest.fixture
    def fake_flac(self, tmp_path: Path) -> Path:
        src = tmp_path / "01 - Track.flac"
        src.write_bytes(b"FAKE-FLAC-CONTENT")
        return src

    def _stub(self, monkeypatch, stderr: str):
        cfg = MagicMock()
        defaults = {
            "lossy_copy.enabled": True,
            "lossy_copy.codec": "opus",
            "lossy_copy.bitrate": "256",
            "lossy_copy.delete_original": False,
        }
        cfg.get.side_effect = lambda key, default=None: defaults.get(key, default)
        monkeypatch.setattr(file_ops, "config_manager", cfg)
        monkeypatch.setattr(file_ops.shutil, "which", lambda _: "/fake/ffmpeg")
        monkeypatch.setattr(
            file_ops.subprocess, "run",
            lambda cmd, **kw: SimpleNamespace(returncode=1, stderr=stderr, stdout=""),
        )

    def test_failure_logs_reason_and_keeps_original(self, monkeypatch, fake_flac, caplog):
        self._stub(monkeypatch, FFMPEG_MISSING_ENCODER)

        with caplog.at_level(logging.ERROR, logger="imports.file_ops"):
            out = file_ops.create_lossy_copy(str(fake_flac))

        assert out is None                       # failure returns None
        assert fake_flac.exists()                # original never deleted on failure
        # The surfaced (ERROR-level) message carries the real reason, not the banner.
        assert "Encoder not found" in caplog.text
        assert "Unknown encoder 'libopus'" in caplog.text
        assert "configuration:" not in caplog.text
