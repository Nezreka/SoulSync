"""the chromecast's HLS: every session cuts its segments at the same source
times (so segment N is the same frames whoever made it), a copied picture is
only copied when its keyframes allow 6 second segments, and a GPU that fails
hands the cast to the CPU. no ffmpeg here: the real run is in
test_hls_real_ffmpeg.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.player import hls_cast as H
from core.player import hwaccel
from core.player import transcode as T

CAST = ({"h264"}, {"aac", "mp3"})


def copied():
    return T.plan("h264", "ac3", quality="cast", height=1080, copyable=CAST)


def converted():
    return T.plan("hevc", "ac3", quality="cast", height=1080, copyable=CAST, codec_facts={"bit_depth": 10})


def test_cut_points_count_from_the_session_first_frame():
    # a session at segment 0: the boundaries themselves
    assert H.window_times(0, 3, first=0.0) == "6.000,12.000,18.000,24.000"
    # a session started by a seek to segment 10, its first frame a keyframe at 61.2:
    # the same source times (66, 72, ...) told as seconds after that frame
    assert H.window_times(10, 12, first=61.2) == "4.800,10.800,16.800"
    # a source that doesn't start at 0
    assert H.window_times(2, 3, first=13.4, t0=1.4) == "6.000,12.000"


def test_a_converted_session_forces_a_keyframe_at_every_cut():
    cmd = H.hls_command("ffmpeg", "in.mkv", converted(), 4, "/tmp/s", enc=hwaccel.CPU, end_seg=6)
    times = "6.000,12.000,18.000"
    assert cmd == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", "24.000",
                   "-t", "24.000", "-i", "in.mkv", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                   "-vf", "scale=-2:'min(1080,ih)',format=yuv420p", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
                   "-force_key_frames", times, "-g", "48", "-sc_threshold", "0",
                   "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-output_ts_offset", "24.000",
                   "-f", "segment", "-segment_format", "mpegts", "-segment_times", times,
                   "-segment_list", os.path.join("/tmp/s", "list.csv"), "-segment_list_type", "csv",
                   "-reset_timestamps", "0", os.path.join("/tmp/s", "seg_%d.ts")]


def test_a_converted_cast_runs_on_the_gpu_with_its_decoder():
    cmd = H.hls_command("ffmpeg", "in.mkv", converted(), 0, "/tmp/s", enc=hwaccel.make("nvenc"), end_seg=2)
    assert cmd[5:9] == ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    assert cmd[cmd.index("-vf") + 1] == "scale_cuda=w=-2:h='min(1080,ih)':format=nv12"
    # session 0 starts at the start: no seek
    assert "-ss" not in cmd and cmd[cmd.index("-output_ts_offset") + 1] == "0.000"


def test_a_copied_session_starts_on_the_keyframe_inside_its_segment():
    cmd = H.hls_command("ffmpeg", "in.mkv", copied(), 10, "/tmp/s", seek=(61.5, 61.2), end_seg=11)
    assert cmd[cmd.index("-ss") + 1] == "61.500"
    assert cmd[cmd.index("-c:v") + 1:cmd.index("-c:v") + 4] == ["copy", "-bsf:v", "h264_mp4toannexb"]
    assert cmd[cmd.index("-segment_times") + 1] == "4.800,10.800"
    assert "-force_key_frames" not in cmd


def test_copy_start_finds_the_keyframe_and_seeks_just_past_it(monkeypatch):
    keys = [0.0, 2.0, 4.0, 6.4, 8.4, 10.4, 12.4, 26.0]
    monkeypatch.setattr(H, "keyframes_between",
                        lambda ff, src, a, b: [k for k in keys if a - 0.001 <= k <= b])
    assert H.copy_start("ffprobe", "x", 0) == (0.0, 0.0)
    seek, key = H.copy_start("ffprobe", "x", 1)
    assert key == 6.4 and seek == pytest.approx(6.7)
    # 12..18 has a keyframe at 12.4 but 18..24 has none: too far apart to copy
    assert H.copy_start("ffprobe", "x", 2)[1] == 12.4
    assert H.copy_start("ffprobe", "x", 3) is None
    # copy_ok looks a minute ahead and turns down the file from segment 0
    assert H.copy_ok("ffprobe", "x", 0) is None


def test_copy_ok_takes_a_steady_keyframe_rhythm(monkeypatch):
    keys = [round(k * 2.002, 3) for k in range(60)]
    monkeypatch.setattr(H, "keyframes_between",
                        lambda ff, src, a, b: [k for k in keys if a - 0.001 <= k <= b])
    assert H.copy_ok("ffprobe", "x", 0) == (0.0, 0.0)
    seek, key = H.copy_ok("ffprobe", "x", 5)
    assert key == pytest.approx(30.03)
    # near the end of the file: the windows past the last keyframe aren't held against it
    keys[:] = [0.0, 2.0, 4.0, 6.0, 8.0]
    assert H.copy_ok("ffprobe", "x", 0) == (0.0, 0.0)


def _sess(tmp_path, start=0, copy=False, first=0.0, proc=None):
    out = tmp_path / f"s{start}{copy}"
    out.mkdir(exist_ok=True)
    return H._HlsSession(key="k", start_seg=start, out_dir=str(out), proc=proc or Proc(None),
                         err_path=out / "ffmpeg.log", copy=copy, first=first)


class Proc:
    def __init__(self, code):
        self.returncode = code
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_a_segment_is_ready_once_ffmpeg_moved_on(tmp_path):
    s = _sess(tmp_path, start=10, copy=True, first=61.2)
    d = Path(s.out_dir)
    (d / "seg_0.ts").write_bytes(b"a")
    # numbered from 0 in its session
    assert s.seg_path(10).name == "seg_0.ts" and not s.ready(10)
    (d / "seg_1.ts").write_bytes(b"b")
    assert s.ready(10) and not s.ready(11)
    (d / "list.csv").write_text("seg_0.ts,0.000000,5.100000\nseg_1.ts,5.100000,11.100000\n")
    assert s.listed() == {10: 0.0, 11: 5.1}
    assert s.ready(11)
    # 61.2 + 5.1 = 66.3: inside segment 11's 66..72
    assert s.in_step(10) and s.in_step(11)
    (d / "list.csv").write_text("seg_0.ts,0.000000,5.100000\nseg_1.ts,5.100000,15.100000\n"
                                "seg_2.ts,15.100000,20.0\n")
    # segment 12 starts at 76.3: past 72..78? no, inside; 76.3 < 78
    assert s.in_step(12)
    (d / "list.csv").write_text("seg_0.ts,0.0,5.1\nseg_1.ts,5.1,15.1\nseg_2.ts,17.9,25.0\n")
    # 61.2 + 17.9 = 79.1: segment 12 should start before 78
    assert not s.in_step(12)
    # a converted session is cut on forced keyframes: never off step
    assert _sess(tmp_path, start=3).in_step(5)


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    """Popen that "runs" an HLS command by writing what each scenario says"""
    monkeypatch.setattr(H, "_sessions", {})
    monkeypatch.setattr(H, "_no_copy", set())
    monkeypatch.setattr(H, "_cpu_only", set())
    monkeypatch.setattr(H, "_ensure_reaper_locked", lambda: None)
    monkeypatch.setattr(H, "WAIT_SEGMENT_TIMEOUT_SEC", 2.0)
    monkeypatch.setattr(H.tempfile, "mkdtemp", lambda prefix="": str(_next_dir(tmp_path)))
    runs = []

    def popen(cmd, stdout=None, stderr=None):
        out_dir = Path(cmd[-1]).parent
        runs.append(cmd)
        return scenario["fn"](cmd, out_dir)

    scenario = {"fn": None, "runs": runs}
    monkeypatch.setattr(H.subprocess, "Popen", popen)
    return scenario


_counter = [0]


def _next_dir(tmp_path):
    _counter[0] += 1
    d = tmp_path / f"hls{_counter[0]}"
    d.mkdir()
    return d


def test_a_long_keyframe_gap_found_later_converts_the_rest(fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(H, "copy_ok", lambda ff, src, seg, t0=0.0: (0.0, 0.0))

    def run(cmd, out_dir):
        if "copy" in cmd:
            # segments 0 and 1 fine, then a 14 second gap: file 2 starts at 26
            for i in range(3):
                (out_dir / f"seg_{i}.ts").write_bytes(b"copy%d" % i)
            (out_dir / "list.csv").write_text("seg_0.ts,0,6.0\nseg_1.ts,6.0,26.0\nseg_2.ts,26.0,32.0\n")
        else:
            (out_dir / "seg_0.ts").write_bytes(b"conv")
            (out_dir / "list.csv").write_text("seg_0.ts,0,6.0\n")
        return Proc(0)

    fake_ffmpeg["fn"] = run
    plan = copied()
    assert H.get_segment_bytes("cast:1", 0, "ffmpeg", "in.mkv", plan) == b"copy0"
    assert H.get_segment_bytes("cast:1", 1, "ffmpeg", "in.mkv", plan) == b"copy1"
    # segment 2 from the copy started at 26s, not inside 12..18: that cast converts from here
    assert H.get_segment_bytes("cast:1", 2, "ffmpeg", "in.mkv", plan, enc=hwaccel.CPU) == b"conv"
    runs = fake_ffmpeg["runs"]
    assert len(runs) == 2 and "-force_key_frames" not in runs[0] and "-force_key_frames" in runs[1]
    assert runs[1][runs[1].index("-ss") + 1] == "12.000"
    assert "cast:1" in H._no_copy
    H.close_all()


def test_a_gpu_that_fails_before_the_first_segment_hands_the_cast_to_the_cpu(fake_ffmpeg, monkeypatch):
    marked = []
    monkeypatch.setattr(hwaccel, "mark_failed", lambda ff, enc, dec, reason, tester=None: marked.append(
        (enc.key, dec, reason)) or "gone")

    def run(cmd, out_dir):
        if "h264_nvenc" in cmd:
            (out_dir / "ffmpeg.log").write_text("[h264_nvenc] OpenEncodeSessionEx failed: out of memory\n")
            return Proc(1)
        (out_dir / "seg_0.ts").write_bytes(b"cpu")
        return Proc(0)

    fake_ffmpeg["fn"] = run
    got = H.get_segment_bytes("cast:2", 0, "ffmpeg", "in.mkv", converted(), enc=hwaccel.make("nvenc"))
    assert got == b"cpu"
    assert marked == [("nvenc", ("hevc", 10), "[h264_nvenc] OpenEncodeSessionEx failed: out of memory")]
    assert "cast:2" in H._cpu_only
    # the cast's next session stays on the CPU
    runs = fake_ffmpeg["runs"]
    assert "libx264" in runs[1]
    H.close_all()


def test_a_cast_reuses_its_session_and_a_far_seek_starts_another(fake_ffmpeg, monkeypatch):
    def run(cmd, out_dir):
        start = float(cmd[cmd.index("-ss") + 1]) if "-ss" in cmd else 0.0
        for i in range(3):
            (out_dir / f"seg_{i}.ts").write_bytes(f"{start}:{i}".encode())
        return Proc(None)  # still running: segments 0-1 are closed by the next one

    fake_ffmpeg["fn"] = run
    plan = converted()
    assert H.get_segment_bytes("c", 0, "ffmpeg", "x", plan, enc=hwaccel.CPU) == b"0.0:0"
    assert H.get_segment_bytes("c", 1, "ffmpeg", "x", plan, enc=hwaccel.CPU) == b"0.0:1"
    assert len(fake_ffmpeg["runs"]) == 1
    assert H.get_segment_bytes("c", 50, "ffmpeg", "x", plan, enc=hwaccel.CPU) == b"300.0:0"
    assert len(fake_ffmpeg["runs"]) == 2
    H.close_all()


def test_the_cast_limit_follows_the_admin_slots(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "_sessions", {})
    monkeypatch.setattr(hwaccel, "settings", lambda: {"slots": 1, "gpu": "auto", "device": "auto"})
    a = _sess(tmp_path, start=0)
    H._sessions["a"] = a
    with H._lock:
        H._make_room_locked()
    assert H._sessions == {} and a.proc.killed
