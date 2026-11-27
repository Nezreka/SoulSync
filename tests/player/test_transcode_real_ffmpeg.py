"""converting on real ffmpeg, and on this box's GPU when it has one.

skipped without ffmpeg; the GPU tests skip when no backend passes a test
encode. first run Nov 27 2026 on a GTX 1080 (nvenc + cuda), ffmpeg 8.0.1."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from core.player import hls_cast as HLS
from core.player import hwaccel
from core.player import tracks as K
from core.player import transcode as T

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg and ffprobe")

BUSY = "testsrc2=size=1920x1080:rate=24:duration={d},noise=alls=25:allf=t+u"


def _run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _make(path, src, *args):
    p = _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src,
              "-f", "lavfi", "-i", "sine=frequency=440:duration=60", "-shortest", *args, str(path)])
    if p.returncode != 0:
        pytest.skip("this ffmpeg can't make the test clip: " + p.stderr.decode("utf-8", "replace")[-200:])
    return str(path)


def _probe(path):
    out = _run([FFPROBE, "-v", "error", "-show_entries",
                "stream=codec_type,codec_name,pix_fmt,height,profile:format=bit_rate,duration",
                "-of", "json", str(path)]).stdout
    return json.loads(out)


def _video(info):
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def _facts(path):
    """what the player learns from the file's header"""
    out = _run([FFPROBE, "-v", "error", "-show_entries",
                "stream=index,codec_type,codec_name,profile,pix_fmt,bits_per_raw_sample,bit_rate,height,width"
                ":format=bit_rate,duration", "-of", "json", str(path)]).stdout
    v = K.parse_probe(json.loads(out))["video"][0]
    return {"codec": v["codec"], "bit_depth": v["bit_depth"], "profile": v["profile"]}


def _to_file(cmd, out):
    """a player command, written to a file instead of the pipe"""
    assert cmd[-1] == "pipe:1"
    return cmd[:-1] + [str(out)]


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    return {
        "hevc10": _make(d / "hevc10.mkv", BUSY.format(d=4), "-c:v", "libx265", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "-c:a", "ac3"),
        "hi10p": _make(d / "hi10p.mkv", BUSY.format(d=3), "-c:v", "libx264", "-preset", "ultrafast",
                       "-pix_fmt", "yuv420p10le", "-c:a", "aac"),
        "gop2": _make(d / "gop2.mkv", "testsrc2=size=640x360:rate=24000/1001:duration=40", "-c:v", "libx264",
                      "-preset", "ultrafast", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", "-c:a", "ac3"),
        "gop10": _make(d / "gop10.mkv", "testsrc2=size=640x360:rate=24:duration=40", "-c:v", "libx264",
                       "-preset", "ultrafast", "-g", "240", "-keyint_min", "240", "-sc_threshold", "0",
                       "-c:a", "ac3"),
    }


@pytest.fixture(autouse=True)
def auto_gpu(monkeypatch):
    monkeypatch.setattr(hwaccel, "settings", lambda: {"slots": 3, "gpu": "auto", "device": "auto"})
    hwaccel.forget()
    yield
    hwaccel.forget()


@pytest.fixture
def gpu():
    enc = hwaccel.detect(FFMPEG)
    if enc.key == "cpu":
        pytest.skip("no GPU encoder works on this box")
    return enc


# ── the CPU, everywhere ─────────────────────────────────────────────────

def test_hevc_10_bit_becomes_8_bit_h264_at_720p(clips, tmp_path):
    p = T.plan("hevc", "ac3", quality="720p", height=1080, codec_facts=_facts(clips["hevc10"]))
    out = tmp_path / "o.mp4"
    assert _run(_to_file(T.command(FFMPEG, clips["hevc10"], p, 0, hwaccel.CPU), out)).returncode == 0
    v = _video(_probe(out))
    assert (v["codec_name"], v["pix_fmt"], int(v["height"])) == ("h264", "yuv420p", 720)


def test_hi10p_is_seen_and_converted(clips, tmp_path):
    facts = _facts(clips["hi10p"])
    assert facts["bit_depth"] == 10
    p = T.plan("h264", "aac", codec_facts=facts)
    assert not p.copy_video and p.reasons == ["10-bit H.264 to 8-bit H.264"]
    out = tmp_path / "o.mp4"
    assert _run(_to_file(T.command(FFMPEG, clips["hi10p"], p, 0, hwaccel.CPU), out)).returncode == 0
    assert _video(_probe(out))["pix_fmt"] == "yuv420p"


def test_the_quality_ceiling_holds_on_the_cpu(clips, tmp_path):
    p = T.plan("hevc", "ac3", quality="480p", height=1080)
    out = tmp_path / "o.mp4"
    assert _run(_to_file(T.command(FFMPEG, clips["hevc10"], p, 0, hwaccel.CPU), out)).returncode == 0
    kbps = int(_probe(out)["format"]["bit_rate"]) / 1000
    assert kbps < 1500 * 1.25, kbps


# ── the GPU, when there is one ──────────────────────────────────────────

def test_the_whole_pipeline_runs_on_the_gpu(gpu):
    got = hwaccel.full_test(FFMPEG, gpu)
    assert got["ok"], got
    if gpu.key in ("nvenc", "vaapi", "qsv"):
        assert got["hw_decode"]


def test_a_gpu_conversion_decodes_on_the_card_and_holds_the_ceiling(gpu, clips, tmp_path):
    p = T.plan("hevc", "ac3", quality="720p", height=1080, codec_facts=_facts(clips["hevc10"]))
    job = T.build(FFMPEG, clips["hevc10"], p, 0, gpu)
    out = tmp_path / "o.mp4"
    run = _run(_to_file(job.cmd, out))
    assert run.returncode == 0, run.stderr[-500:]
    v = _video(_probe(out))
    assert (v["codec_name"], v["pix_fmt"], int(v["height"])) == ("h264", "yuv420p", 720)
    kbps = int(_probe(out)["format"]["bit_rate"]) / 1000
    assert kbps < 4000 * 1.25, kbps


def test_a_decode_the_gpu_refuses_runs_again_on_the_cpu(gpu, clips, monkeypatch):
    """tell the planner the 10-bit h264 clip is 8 bit, so the GPU is asked
    to decode it. NVDEC can't (checked on a GTX 1080): the stream comes
    from the CPU instead, the viewer gets bytes, and that decode is
    remembered. a GPU that can decode it just plays it."""
    monkeypatch.setattr(T, "_slots", T._Slots())
    p = T.plan("h264", "aac", quality="720p", height=1080, codec_facts={"codec": "h264", "bit_depth": 8})
    job = T.build(FFMPEG, clips["hi10p"], p, 0, gpu)
    if not job.hw_decode:
        pytest.skip(f"{gpu.label} doesn't decode h264 on the card")
    run = T.stream(job)
    data = b"".join(run)
    assert data[4:8] == b"ftyp" and len(data) > 10_000
    if run.used["fell_back"]:
        assert run.used["encoder"] == "CPU"
        assert not hwaccel.can_decode(gpu.key, "h264", 8)
        assert hwaccel.status(None)["last_fallback"]["backend"] == gpu.key


# ── chromecast HLS ──────────────────────────────────────────────────────

def _first_pts(path):
    out = _run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                "-of", "csv=p=0", "-read_intervals", "%+#1", str(path)]).stdout.decode()
    return float(out.split()[0].strip(","))


def _session(src, plan, start, out_dir, enc=None, seek=None):
    out_dir.mkdir()
    cmd = HLS.hls_command(FFMPEG, src, plan, start, str(out_dir), enc=enc, seek=seek, end_seg=start + 4)
    run = _run(cmd)
    assert run.returncode == 0, run.stderr[-500:]
    return {start + i: _first_pts(out_dir / f"seg_{i}.ts") for i in range(5) if (out_dir / f"seg_{i}.ts").exists()}


@pytest.mark.parametrize("copy", [True, False])
def test_every_session_cuts_the_same_frames(clips, tmp_path, copy):
    """a cast that seeks gets a new ffmpeg; its segments must be the ones
    the first session would have made, or the cast skips or repeats"""
    if copy:
        plan = T.plan("h264", "ac3", quality="cast", height=360, copyable=({"h264"}, {"aac"}))
        assert plan.copy_video
        seeks = {0: HLS.copy_ok(FFPROBE, clips["gop2"], 0), 2: HLS.copy_ok(FFPROBE, clips["gop2"], 2)}
        assert seeks[0] and seeks[2]
    else:
        plan = T.plan("hevc", "ac3", quality="cast", height=360, copyable=({"h264"}, {"aac"}))
        seeks = {0: None, 2: None}
    a = _session(clips["gop2"], plan, 0, tmp_path / "a", hwaccel.CPU, seeks[0])
    b = _session(clips["gop2"], plan, 2, tmp_path / "b", hwaccel.CPU, seeks[2])
    for k in (2, 3, 4):
        # the same frame starts segment k in both (mpegts adds the same delay to both;
        # a session from 0 differs by a constant under a frame for copies, 0.13s converted)
        assert abs(a[k] - b[k]) < 0.2, (k, a, b)
    # and each starts inside its own 6 seconds (plus mpegts' 1.4s delay)
    for k, pts in b.items():
        assert 6 * k <= pts - 1.4 < 6 * k + 2.1, (k, pts)


def test_a_long_gap_between_keyframes_is_converted_not_copied(clips, tmp_path, monkeypatch):
    assert HLS.copy_ok(FFPROBE, clips["gop10"], 0) is None
    assert HLS.copy_ok(FFPROBE, clips["gop2"], 0) == (0.0, pytest.approx(0.0, abs=0.1))
    monkeypatch.setattr(HLS, "_sessions", {})
    monkeypatch.setattr(HLS, "_no_copy", set())
    plan = T.plan("h264", "ac3", quality="cast", height=360, copyable=({"h264"}, {"aac"}))
    monkeypatch.setattr(T, "ffprobe_for", lambda ff: FFPROBE)
    try:
        seg3 = HLS.get_segment_bytes("real:gop10", 3, FFMPEG, clips["gop10"], plan, enc=hwaccel.CPU)
        assert seg3 and seg3[0] == 0x47  # an mpegts packet
        sess = HLS._sessions["real:gop10"]
        assert sess.copy is False
        assert 18 <= _first_pts(sess.seg_path(3)) - 1.4 < 18.2
    finally:
        HLS.close_all()
