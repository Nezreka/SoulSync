"""the music apps' transcoder (core/subsonic/transcode.py): raw or
converted, the ffmpeg command, the slot cap, and ffmpeg killed when the
listener goes away. a real conversion runs only where ffmpeg is found."""

from __future__ import annotations

import subprocess
import sys

import pytest

from core.subsonic import transcode as X


@pytest.fixture(autouse=True)
def _fresh():
    X._reset_for_tests()
    yield
    X._reset_for_tests()


@pytest.mark.parametrize("kw,raw,fmt,kbps", [
    (dict(suffix="flac", source_kbps=900, fmt="raw", max_kbps=128), True, "", 0),
    (dict(suffix="mp3", source_kbps=320), True, "", 0),
    (dict(suffix="mp3", source_kbps=128, max_kbps=192), True, "", 0),
    (dict(suffix="mp3", source_kbps=320, max_kbps=128), False, "mp3", 128),
    (dict(suffix="flac", source_kbps=900, max_kbps=320), False, "mp3", 320),
    (dict(suffix="flac", source_kbps=900, fmt="mp3"), False, "mp3", 192),
    (dict(suffix="flac", source_kbps=900, fmt="opus", max_kbps=96), False, "opus", 96),
    (dict(suffix="flac", source_kbps=900, fmt="mp3", max_kbps=100), False, "mp3", 96),
    (dict(suffix="mp3", source_kbps=128, fmt="mp3"), True, "", 0),
    (dict(suffix="flac", source_kbps=900, fmt="wma"), True, "", 0),
    (dict(suffix="flac", source_kbps=900, max_kbps=128, have_ffmpeg=False), True, "", 0),
    (dict(suffix="flac", source_kbps=900, max_kbps=128, default_fmt="opus"), False, "opus", 128),
])
def test_decide(kw, raw, fmt, kbps):
    plan = X.decide(**kw)
    assert plan.raw is raw
    if not raw:
        assert (plan.fmt, plan.kbps) == (fmt, kbps)
        assert plan.mime == X.FORMATS[fmt][2]


def test_the_command_seeks_and_pipes():
    cmd = X.command("ffmpeg", "/m/a.flac", X.Plan(raw=False, fmt="mp3", kbps=128, mime="audio/mpeg"), 30)
    assert cmd[cmd.index("-ss") + 1] == "30" and cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-b:a") + 1] == "128k" and cmd[-1] == "pipe:1"
    assert "-ss" not in X.command("ffmpeg", "/m/a.flac", X.Plan(raw=False, fmt="opus", kbps=96), 0)


def test_estimated_length():
    assert X.estimate_length(200, 128) == 200 * 128 * 1000 // 8
    assert X.estimate_length(200, 128, 50) == 150 * 128 * 1000 // 8


# a stand-in for ffmpeg: writes forever until killed
_FOREVER = [sys.executable, "-c", "import sys\nwhile True: sys.stdout.buffer.write(b'x'*65536); sys.stdout.flush()"]


def _fake_popen(procs):
    def popen(cmd, **kw):
        p = subprocess.Popen(_FOREVER, **kw)
        procs.append(p)
        return p
    return popen


def test_closing_the_stream_kills_ffmpeg_and_frees_the_slot(monkeypatch):
    monkeypatch.setattr(X, "ffmpeg", lambda: "ffmpeg")
    X._reset_for_tests(max_jobs=1)
    procs = []
    plan = X.Plan(raw=False, fmt="mp3", kbps=128, mime="audio/mpeg")
    chunks = X.open_stream("/m/a.flac", plan, popen=_fake_popen(procs))
    assert len(next(chunks)) > 0
    # the one slot is taken
    with pytest.raises(X.Busy):
        X.open_stream("/m/b.flac", plan, popen=_fake_popen(procs))
    chunks.close()                       # the listener hung up
    assert procs[0].poll() is not None   # killed
    again = X.open_stream("/m/c.flac", plan, popen=_fake_popen(procs))
    next(again)
    again.close()
    assert procs[-1].poll() is not None


def test_no_ffmpeg_is_an_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(X, "ffmpeg", lambda: None)
    with pytest.raises(OSError):
        X.open_stream("/m/a.flac", X.Plan(raw=False, fmt="mp3", kbps=128))


def test_a_real_conversion(tmp_path):
    binary = X.ffmpeg()
    if not binary:
        pytest.skip("no ffmpeg here")
    src = tmp_path / "tone.wav"
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", str(src)], check=True)
    plan = X.decide(suffix="wav", source_kbps=1411, max_kbps=64)
    data = b"".join(X.open_stream(str(src), plan, 1))
    assert data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert 0 < len(data) < 3 * 64 * 1000 // 8 + 4096
