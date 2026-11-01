"""Streamed Tidal AAC read 0:00 and wouldn't seek in Navidrome/Feishin: the
segments make a fragmented MP4 whose header carries no duration. The remux
turns it into a plain MP4 with its real length."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen.mp4 import MP4

from core.downloads.mp4_remux import remux_to_plain_mp4

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


@needs_ffmpeg
def test_a_fragmented_aac_gets_its_duration(tmp_path):
    path = tmp_path / "track.m4a"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=12",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-frag_duration", "2000000", str(path)], check=True)
    assert MP4(str(path)).info.length == 0  # what Navidrome saw

    assert remux_to_plain_mp4(path, FFMPEG) is True
    assert MP4(str(path)).info.length == pytest.approx(12, abs=0.2)
    assert not (tmp_path / "track.remux.m4a").exists()


def test_no_ffmpeg_leaves_the_file_alone(tmp_path):
    path = tmp_path / "track.m4a"
    path.write_bytes(b"streamed bytes")
    assert remux_to_plain_mp4(path, None) is False
    assert path.read_bytes() == b"streamed bytes"


def test_a_failed_remux_keeps_the_original(tmp_path):
    path = tmp_path / "track.m4a"
    path.write_bytes(b"not audio at all")
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    assert remux_to_plain_mp4(path, str(fake)) is False
    assert path.read_bytes() == b"not audio at all"
    assert not (tmp_path / "track.remux.m4a").exists()
