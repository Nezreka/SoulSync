"""#2118: replaygain failed with "'utf-8' codec can't decode byte 0xe2".

ffmpeg prints a file's tags to stderr as raw bytes. a tag written in
latin-1 / cp1252 is not valid utf-8, and subprocess text mode decoded
stderr strictly, so the whole analysis blew up before we parsed a line.
these run a fake ffmpeg that prints such a tag, so the real decode path
is exercised.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

_BAD_TAG = b"    title           : Papercut \xe2\x80 Linkin Park\n"

_STDERR = (
    b"Input #0, flac, from 'x.flac':\n  Metadata:\n" + _BAD_TAG
    + b"size=N/A time=00:03:04.50 bitrate=N/A speed= 300x\n"
    + b"[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
    + b"    I:         -8.9 LUFS\n\n  True peak:\n    Peak:        0.3 dBFS\n"
)


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = tmp_path / "stderr.bin"
    payload.write_bytes(_STDERR)
    script = bin_dir / "ffmpeg"
    # sh, not python: a venv path with spaces can't sit on a shebang line
    script.write_text(f"#!/bin/sh\ncat '{payload}' >&2\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake ffmpeg")
def test_replaygain_analysis_survives_non_utf8_tags(fake_ffmpeg, tmp_path):
    from core.replaygain import analyze_track

    lufs, peak = analyze_track(str(tmp_path / "x.flac"))
    assert lufs == -8.9
    assert peak == 0.3


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake ffmpeg")
def test_decoded_duration_probe_survives_non_utf8_tags(fake_ffmpeg, tmp_path, monkeypatch):
    from core.imports import file_integrity

    monkeypatch.setattr(file_integrity, "_find_ffmpeg", lambda: fake_ffmpeg)
    assert file_integrity.probe_decoded_duration(str(tmp_path / "x.flac")) == pytest.approx(184.5)


def test_every_ffmpeg_text_mode_call_tolerates_bad_bytes():
    """guard: any core/api module that shells out to ffmpeg/ffprobe in text
    mode must decode with errors= set, or one odd tag kills the call."""
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("core", "api"):
        for path in (root / folder).rglob("*.py"):
            src = path.read_text(encoding="utf-8", errors="replace")
            if "ffmpeg" not in src and "ffprobe" not in src:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(r"\btext=True\b", line) and "errors=" not in line:
                    offenders.append(f"{path.relative_to(root)}:{i}")
    assert offenders == []
