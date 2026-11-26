"""a lossless file made from a lossy one is caught, and a genuine one never is.

the numbers in the first test are the ones measured on real files when the
rule was set (see core/imports/lossless_check.py).
"""

import shutil
import subprocess

import pytest

from core.imports import lossless_check as lc


@pytest.mark.parametrize("label,mid,top,fake", [
    ("genuine, bright", -52.7, -43.9, False),
    ("genuine, dull", -68.3, -58.8, False),
    ("genuine, tape-like roll-off", -55.9, -77.8, False),
    ("genuine, nothing up there at all", -77.5, -91.0, False),
    ("from mp3 96", -54.0, -91.0, True),
    ("from mp3 128", -53.2, -91.0, True),
    ("from aac 128", -53.0, -91.0, True),
    ("from mp3 192: beside a tape transfer, passes", -53.0, -78.5, False),
    ("from mp3 320: beside any soft master, passes", -52.7, -56.8, False),
    ("could not be measured", None, -91.0, False),
    ("could not be measured", -53.0, None, False),
])
def test_the_verdict_on_measured_levels(label, mid, top, fake):
    assert bool(lc.judge(mid, top)) is fake, label


def test_only_lossless_files_are_looked_at(tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    assert lc.detect_lossy_transcode(str(mp3)) is None
    assert lc.detect_lossy_transcode(str(tmp_path / "missing.flac")) is None


def _ffmpeg(*args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, timeout=180).returncode == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_on_real_files_a_128_kbps_transcode_is_caught_and_the_source_is_not(tmp_path):
    real = tmp_path / "real.flac"
    if not _ffmpeg("-f", "lavfi", "-i", "anoisesrc=color=pink:duration=40:sample_rate=44100:amplitude=0.5",
                   "-ac", "2", str(real)):
        pytest.skip("ffmpeg could not make the test file")
    assert lc.detect_lossy_transcode(str(real)) is None

    mp3 = tmp_path / "lossy.mp3"
    fake = tmp_path / "fake.flac"
    if not (_ffmpeg("-i", str(real), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3))
            and _ffmpeg("-i", str(mp3), str(fake))):
        pytest.skip("this ffmpeg has no mp3 encoder")
    reason = lc.detect_lossy_transcode(str(fake))
    assert reason and "lossy source" in reason

    # a soft master is genuine: a gentle roll-off, not a wall
    soft = tmp_path / "soft.flac"
    assert _ffmpeg("-f", "lavfi", "-i", "anoisesrc=color=pink:duration=40:sample_rate=44100:amplitude=0.5",
                   "-af", "lowpass=f=14000", "-ac", "2", str(soft))
    assert lc.detect_lossy_transcode(str(soft)) is None
