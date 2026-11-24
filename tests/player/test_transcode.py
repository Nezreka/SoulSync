"""the player's transcoder: copy what a browser plays, convert the rest,
from any start, on the best encoder that works, and always let ffmpeg go."""

from __future__ import annotations

import json

import pytest
from flask import Flask

from core.player import transcode as T
from core.player.playable import Source


def test_plan_copies_what_plays_and_converts_the_rest():
    ac3 = T.plan("h264", "ac3")
    assert (ac3.copy_video, ac3.copy_audio, ac3.needed) == (True, False, True)
    assert ac3.reasons == ["AC3 audio to AAC"]
    hevc = T.plan("hevc", "aac")
    assert (hevc.copy_video, hevc.copy_audio) == (False, True)
    assert T.plan("h264", "aac").needed is False
    # unknown converts: a guess that fails is a silent picture
    assert T.plan(None, None).copy_audio is False


def test_a_smaller_quality_shrinks_and_hdr_is_tonemapped():
    small = T.plan("h264", "aac", quality="720p", height=1080)
    assert small.copy_video is False and small.reasons == ["H264 video to H.264 at 720p"]
    # already that small: nothing to shrink
    assert T.plan("h264", "aac", quality="720p", height=480).copy_video is True
    assert T.plan("hevc", "aac", hdr="HDR10").tonemap is True
    assert T.plan("h264", "aac", hdr="HDR10").tonemap is False  # copied: left alone
    assert T.plan("h264", "aac", quality="8k").quality == "original"


def test_the_command():
    cpu = ("libx264", "CPU", [], "")
    copy = T.command("ffmpeg", "/m/a.mkv", T.plan("h264", "ac3"), 0, cpu)
    assert copy[copy.index("-c:v") + 1] == "copy"
    assert copy[copy.index("-c:a") + 1] == "aac" and "-ss" not in copy
    assert copy[-3:] == ["-f", "mp4", "pipe:1"]
    seek = T.command("ffmpeg", "/m/a.mkv", T.plan("hevc", "aac", quality="720p"), 754.5, cpu)
    assert seek[seek.index("-ss") + 1] == "754.500" and seek.index("-ss") < seek.index("-i")
    assert seek[seek.index("-c:v") + 1] == "libx264"
    assert "min(720,ih)" in seek[seek.index("-vf") + 1]
    assert seek[seek.index("-maxrate") + 1] == "4M"
    hdr = T.plan("hevc", "aac", hdr="HDR10")
    assert "tonemap" in T.command("ffmpeg", "x", hdr, 0, cpu, can_tonemap=True)[T.command("ffmpeg", "x", hdr, 0, cpu, can_tonemap=True).index("-vf") + 1]
    # no zscale in this ffmpeg: it still plays, untouched
    assert "tonemap" not in " ".join(T.command("ffmpeg", "x", hdr, 0, cpu, can_tonemap=False))
    gpu = ("h264_vaapi", "VA-API GPU", ["-vaapi_device", "/dev/dri/renderD128"], ",format=nv12,hwupload")
    vaapi = T.command("ffmpeg", "x", T.plan("hevc", "aac"), 0, gpu)
    assert vaapi.index("-vaapi_device") < vaapi.index("-i")
    assert vaapi[vaapi.index("-vf") + 1].endswith(",hwupload")


def test_the_first_working_encoder_wins_and_cpu_is_the_floor(monkeypatch):
    monkeypatch.setattr(T, "_encoder_checked", False)
    monkeypatch.setattr(T, "_works", lambda ff, name, pre, tail: name == "h264_qsv")
    assert T.encoder("ffmpeg")[0] == "h264_qsv"
    monkeypatch.setattr(T, "_encoder_checked", False)
    monkeypatch.setattr(T, "_works", lambda *a: False)
    assert T.encoder("ffmpeg")[0] == "libx264"


class FakeProc:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.killed = False
        self.returncode = None

        class Out:
            def read(_, n):
                return self.chunks.pop(0) if self.chunks else b""

        class Err:
            def read(_):
                return b""

        self.stdout, self.stderr = Out(), Err()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_slots_are_freed_whether_the_viewer_watches_or_leaves(monkeypatch):
    procs = []
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(1))
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: procs.append(FakeProc([b"ab", b"cd"])) or procs[-1])
    run = T.stream(["ffmpeg"])
    with pytest.raises(T.Busy):
        T.stream(["ffmpeg"])
    # left before the first byte: close still kills it and frees the slot
    run.close()
    assert procs[0].killed
    watched = T.stream(["ffmpeg"])
    assert b"".join(watched) == b"abcd"
    T.stream(["ffmpeg"]).close()  # the slot came back after the end


def test_a_screen_runs_one_conversion_and_hopping_frees_the_last(monkeypatch):
    """channel hopping on one screen filled every slot and the next channel
    came up black (503, nov 21 2026). a screen's new conversion stops its
    last; another screen's keeps running"""
    procs = []
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(2))
    monkeypatch.setattr(T, "_owners", {})
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: procs.append(FakeProc([b"ab"])) or procs[-1])
    tv = T.stream(["ffmpeg"], owner="1:tv")
    T.stream(["ffmpeg"], owner="1:phone")
    for _ in range(5):  # hop, hop, hop: never busy
        tv = T.stream(["ffmpeg"], owner="1:tv")
    assert [p.killed for p in procs] == [True, False, True, True, True, True, False]
    # with no owner, the old rule: busy when full
    with pytest.raises(T.Busy):
        T.stream(["ffmpeg"])
    tv.close()
    assert T._owners.keys() == {"1:phone"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.player_stream as S
    import core.player.playable as P
    sources = {
        "ac3": Source("file", "video/x-matroska", path=str(tmp_path / "a.mkv"), duration=100.0,
                      verdict={"verdict": "no", "reasons": ["ac3"]}, extra={"file_id": 7},
                      private={"video_codec": "h264", "audio_codec": "ac3", "height": 1080}),
        "fine": Source("file", "video/mp4", path=str(tmp_path / "b.mp4"), duration=100.0,
                       verdict={"verdict": "yes"}, extra={"file_id": 8},
                       private={"video_codec": "h264", "audio_codec": "aac", "height": 1080}),
    }
    monkeypatch.setattr(P, "resolve", lambda t, i, part=None: sources[i])
    monkeypatch.setattr(T, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(T, "encoder", lambda ff: ("h264_nvenc", "NVIDIA GPU", [], ""))
    monkeypatch.setattr(T, "has_filter", lambda ff, name: True)
    app = Flask(__name__)
    app.register_blueprint(S.create_blueprint())
    return app.test_client()


def test_playable_offers_the_converted_stream_only_when_needed(client):
    body = client.get("/api/player/playable/movie/ac3").get_json()
    assert body["url"] == "/api/player/transcode/movie/ac3?q=original&part=7"
    assert body["transcode"] == {"video": "copy", "audio": "aac", "reasons": ["AC3 audio to AAC"],
                                 "quality": "original", "tonemap": False, "encoder": None}
    fine = client.get("/api/player/playable/movie/fine").get_json()
    assert "transcode" not in fine and fine["url"].startswith("/api/player/stream/")
    assert fine["fallback_url"] == "/api/player/transcode/movie/fine?q=original&part=8"
    smaller = client.get("/api/player/playable/movie/fine?q=480p").get_json()
    assert smaller["transcode"]["encoder"] == "NVIDIA GPU" and "q=480p" in smaller["url"]


def test_transcode_streams_and_says_when_it_is_full(client, monkeypatch):
    seen = []
    monkeypatch.setattr(T, "stream", lambda cmd, owner=None: seen.append(cmd) or iter([b"mp4"]))
    res = client.get("/api/player/transcode/movie/ac3?t=90&q=original")
    assert res.status_code == 200 and res.data == b"mp4" and res.mimetype == "video/mp4"
    assert seen[0][seen[0].index("-ss") + 1] == "90.000"

    def busy(cmd, owner=None):
        raise T.Busy()

    monkeypatch.setattr(T, "stream", busy)
    full = client.get("/api/player/transcode/movie/ac3")
    assert full.status_code == 503 and json.loads(full.data)["error"] == "busy"
    assert client.get("/api/player/transcode/track/ac3").status_code == 400


def test_the_seek_lands_on_the_keyframe_before(monkeypatch):
    assert T.parse_keyframes("0.000000\n2.002000,\nnope\n4.004000\n") == [0.0, 2.002, 4.004]
    assert T.ffprobe_for("C:/ff/bin/ffmpeg.EXE").endswith("ffprobe.exe")
    assert T.ffprobe_for("ffmpeg") == "ffprobe"

    class Done:
        stdout = "120.0\n122.0\n122.4\n130.0\n"

    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: Done())
    # 123.5: the keyframe at 122.0 starts it; ffmpeg seeks a bit past, under halfway to 122.4
    assert T.seek_point("ffprobe", "x", 121.0) == (120.0, 120.3)
    start, seek = T.seek_point("ffprobe", "x", 122.2)
    assert start == 122.0 and seek == pytest.approx(122.2)
    assert T.seek_point("ffprobe", "x", 0) == (0.0, 0.0)

    def broken(*a, **k):
        raise OSError("no ffprobe")

    monkeypatch.setattr(T.subprocess, "run", broken)
    assert T.seek_point("ffprobe", "x", 50.0) == (50.0, 50.0)


def test_start_snaps_only_when_the_video_is_copied(client, monkeypatch):
    monkeypatch.setattr(T, "seek_point", lambda probe, src, t: (118.0, 118.3))
    body = client.get("/api/player/transcode/movie/ac3/start?t=121").get_json()
    assert (body["start"], body["seek"]) == (118.0, 118.3)
    # converting the video starts frame exact
    fine = client.get("/api/player/transcode/movie/fine/start?t=121&q=480p").get_json()
    assert (fine["start"], fine["seek"]) == (121.0, 121.0)


def test_another_audio_track_or_burned_subtitles_always_convert(client, monkeypatch):
    import api.player_tracks as R
    tracks = {"audio": [{"codec": "aac"}, {"codec": "ac3"}], "subtitles": []}
    monkeypatch.setattr(R, "source_tracks", lambda s: (tracks, "x", "ffmpeg", "k"))
    jp = client.get("/api/player/playable/movie/fine?audio=1").get_json()
    assert jp["url"].endswith("&audio=1") and jp["transcode"]["audio"] == "aac"
    assert jp["transcode"]["reasons"] == ["AC3 audio to AAC"]
    burned = client.get("/api/player/playable/movie/fine?burn=0").get_json()
    assert burned["url"].endswith("&burn=0") and burned["transcode"]["video"] == "h264"


def test_audio_sync_moves_the_sound():
    cpu = ("libx264", "CPU", [], "")
    p = T.plan("h264", "aac", delay_ms=250)
    assert p.copy_video and "sound moved later by 250 ms" in p.reasons
    cmd = T.command("ffmpeg", "x.mkv", p, 60, cpu)
    # the sound itself moves (a timestamp shift would be undone by the muxer)
    assert cmd[cmd.index("-af") + 1] == "adelay=250:all=1"
    assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-c:v") + 1] == "copy"
    early = T.command("ffmpeg", "x.mkv", T.plan("h264", "aac", delay_ms=-400), 0, cpu)
    assert early[early.index("-af") + 1] == "atrim=start=0.400,asetpts=PTS-STARTPTS"
    assert T.plan("h264", "aac", delay_ms=-999_999).delay_ms == -10_000


def test_audio_sync_always_converts(client):
    body = client.get("/api/player/playable/movie/fine?delay=-300").get_json()
    assert body["url"].endswith("&delay=-300") and "sound moved earlier by 300 ms" in body["transcode"]["reasons"]


def test_a_noisy_ffmpeg_does_not_stall(tmp_path):
    """stderr was a pipe nobody read until the end: past the pipe's buffer
    (4 KB on windows, 64 KB on linux) ffmpeg blocked writing its log and the
    viewer's stream froze while it held a slot."""
    import sys
    import threading

    from core.player import transcode as tc

    script = ("import sys; sys.stderr.write('frame= warn ' * 60000); sys.stderr.flush(); "
              "sys.stdout.write('done'); sys.stdout.flush()")
    out = []

    def run():
        body = tc.stream([sys.executable, "-c", script])
        out.append(b"".join(body))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=20)
    assert out == [b"done"]
