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
    assert copy == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "/m/a.mkv",
                    "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    seek = T.command("ffmpeg", "/m/a.mkv", T.plan("hevc", "aac", quality="720p"), 754.5, cpu)
    assert seek == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", "754.500",
                    "-i", "/m/a.mkv", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                    "-vf", "scale=-2:'min(720,ih)',format=yuv420p", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "21", "-profile:v", "high", "-maxrate", "4M", "-bufsize", "4M",
                    "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", "-c:a", "copy",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    hdr = T.plan("hevc", "aac", hdr="HDR10")
    mapped = T.command("ffmpeg", "x", hdr, 0, cpu, can_tonemap=True)
    assert "tonemap" in mapped[mapped.index("-vf") + 1]
    # no zscale in this ffmpeg: it still plays, untouched
    assert "tonemap" not in " ".join(T.command("ffmpeg", "x", hdr, 0, cpu, can_tonemap=False))
    # an old-style VA-API tuple gets the backend's own device setup and the GPU scaler
    gpu = ("h264_vaapi", "VA-API GPU", ["-vaapi_device", "/dev/dri/renderD129"], ",format=nv12,hwupload")
    vaapi = T.command("ffmpeg", "x", T.plan("hevc", "aac"), 0, gpu)
    assert vaapi[:vaapi.index("-i")] == [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-init_hw_device", "vaapi=va:/dev/dri/renderD129", "-filter_hw_device", "va",
        "-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"]
    assert vaapi[vaapi.index("-vf") + 1] == "scale_vaapi=w=-2:h='min(1080,ih)':format=nv12"


def test_the_first_working_encoder_wins_and_cpu_is_the_floor(monkeypatch):
    from core.player import hwaccel
    monkeypatch.setattr(hwaccel, "settings", lambda: {"slots": 3, "gpu": "auto", "device": "auto"})
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    monkeypatch.setattr(hwaccel, "render_devices", lambda: ["/dev/dri/renderD128"])
    hwaccel.forget()
    monkeypatch.setattr(hwaccel, "try_encode", lambda ff, enc: (enc.key == "qsv", "no"))
    assert T.encoder("ffmpeg")[0] == "h264_qsv"
    hwaccel.forget()
    monkeypatch.setattr(hwaccel, "try_encode", lambda ff, enc: (False, "no device"))
    assert T.encoder("ffmpeg")[0] == "libx264"
    hwaccel.forget()


# ── the plan: 10-bit h264 and a bitrate ceiling on copies ───────────────

def test_a_copy_over_the_quality_bitrate_is_converted_down_to_it():
    big = {"bitrate_kbps": 30_000}
    p = T.plan("h264", "aac", quality="1080p", height=1080, codec_facts=big)
    assert p.copy_video is False
    assert p.reasons == ["H264 video to H.264 at 1080p (the source's 30 Mbps is over 8 Mbps)"]
    # under the ceiling: copied as before
    assert T.plan("h264", "aac", quality="1080p", height=1080, codec_facts={"bitrate_kbps": 6_000}).copy_video
    # original has no ceiling
    assert T.plan("h264", "aac", quality="original", height=1080, codec_facts=big).copy_video
    # a 720p source at 720p (no shrink) still gets the 4M ceiling
    assert not T.plan("h264", "aac", quality="720p", height=720, codec_facts={"bitrate_kbps": 9_000}).copy_video
    # and the conversion it gets is capped
    cmd = T.command("ffmpeg", "x", p, 0)
    assert cmd[cmd.index("-maxrate") + 1] == "8M"


def test_h264_only_a_pc_decodes_is_converted():
    ten = T.plan("h264", "aac", codec_facts={"bit_depth": 10})
    assert ten.copy_video is False and ten.reasons == ["10-bit H.264 to 8-bit H.264"]
    assert ten.src_bit_depth == 10
    # the profile alone says so when the depth isn't known
    assert not T.plan("h264", "aac", codec_facts={"profile": "High 10"}).copy_video
    assert T.plan("h264", "aac", codec_facts={"profile": "High 4:4:4 Predictive"}).reasons == [
        "4:4:4 H.264 to 4:2:0 H.264"]
    assert T.plan("h264", "aac", codec_facts={"pix_fmt": "yuv422p"}).reasons == ["4:2:2 H.264 to 4:2:0 H.264"]
    # 8-bit high profile plays
    assert T.plan("h264", "aac", codec_facts={"bit_depth": 8, "profile": "High"}).copy_video
    # hevc 10-bit isn't h264's business: the codec table decides
    assert T.plan("hevc", "aac", codec_facts={"bit_depth": 10}).reasons == ["HEVC video to H.264"]


def test_a_chromecast_plan_copies_what_it_plays():
    cast = ({"h264"}, {"aac", "mp3"})
    ok = T.plan("h264", "aac", quality="cast", height=1080, copyable=cast)
    assert ok.needed is False
    vp9 = T.plan("vp9", "opus", quality="cast", height=1080, copyable=cast)
    assert (vp9.copy_video, vp9.copy_audio) == (False, False)
    # 4k h264 comes down to 1080p, HDR is tone mapped
    uhd = T.plan("hevc", "eac3", quality="cast", height=2160, hdr="HDR10", copyable=cast)
    assert uhd.tonemap and uhd.reasons[0] == "HEVC video to H.264 at 1080p"
    assert T.plan("h264", "aac", quality="cast", height=2160, copyable=cast).copy_video is False
    # a cast of a file with no height on record copies it; a picked quality still shrinks
    assert T.plan("h264", "aac", quality="cast", height=None, copyable=cast).copy_video
    assert not T.plan("h264", "aac", quality="720p", height=None).copy_video
    # hi10p on a chromecast: converted
    assert not T.plan("h264", "aac", quality="cast", height=1080, copyable=cast,
                      codec_facts={"bit_depth": 10}).copy_video


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
    monkeypatch.setattr(T, "stream", lambda job, owner=None: seen.append(job.cmd) or iter([b"mp4"]))
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


def test_an_admin_stop_ends_the_screen_conversion(monkeypatch):
    procs = []
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(2))
    monkeypatch.setattr(T, "_owners", {})
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: procs.append(FakeProc([b"ab"])) or procs[-1])
    T.stream(["ffmpeg"], owner="1:tv")
    T.stream(["ffmpeg"], owner="1:phone")
    assert T.stop_owner("1:tv") is True and T.stop_owner("1:tv") is False
    assert [p.killed for p in procs] == [True, False] and T._owners.keys() == {"1:phone"}


def test_a_stopped_screen_item_is_not_served_for_a_while(client, monkeypatch):
    """an admin stopped this profile on this film (server activity): asking
    again for it is refused, with the admin's words; other films still play"""
    from core.player import presence
    monkeypatch.setattr("core.profile_context.get_current_profile_id", lambda: 1)
    monkeypatch.setattr(T, "stream", lambda cmd, owner=None: iter([b"mp4"]))
    presence._sessions.clear()
    presence._stops.clear()
    presence.beat(1, {"session": "tv", "item_type": "movie", "item_id": "ac3", "position": 1, "duration": 100,
                      "playing": True})
    presence.order_stop(1, "tv", "Bedtime")
    try:
        for url in ("/api/player/playable/movie/ac3", "/api/player/stream/movie/ac3",
                    "/api/player/transcode/movie/ac3"):
            res = client.get(url)
            assert res.status_code == 403, url
            assert res.get_json() == {"success": False, "error": "stopped", "message": "Bedtime"}
        assert client.get("/api/player/playable/movie/fine").status_code == 200
    finally:
        presence._sessions.clear()
        presence._stops.clear()


# ── a GPU that fails before the first byte: the CPU's turn ──────────────

class DeadProc(FakeProc):
    """a GPU ffmpeg that wrote nothing and exited with an error"""

    def __init__(self):
        super().__init__([])
        self.returncode = 1


def _gpu_job(cmd=("gpu",), hw_decode=None):
    from core.player import hwaccel
    return T.Job(cmd=list(cmd), enc=hwaccel.make("nvenc"), hw_decode=hw_decode, cpu_cmd=["cpu"], ffmpeg="ffmpeg")


def test_a_gpu_job_that_dies_first_runs_again_on_the_cpu(monkeypatch):
    from core.player import hwaccel
    started, marked = [], []
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(1))
    monkeypatch.setattr(T, "_owners", {})

    def popen(argv, **k):
        started.append(argv[0])
        return DeadProc() if argv[0] == "gpu" else FakeProc([b"cpu-bytes"])

    monkeypatch.setattr(T.subprocess, "Popen", popen)
    monkeypatch.setattr(hwaccel, "mark_failed",
                        lambda ff, enc, dec, reason, tester=None: marked.append((enc.key, dec)) or "NVIDIA GPU fell over")
    run = T.stream(_gpu_job(hw_decode=("h264", 10)), owner="1:tv")
    assert T.running("1:tv") == {"encoder": "NVIDIA GPU", "hw_decode": True, "fell_back": None}
    assert b"".join(run) == b"cpu-bytes"
    assert started == ["gpu", "cpu"] and marked == [("nvenc", ("h264", 10))]
    # the slot it held is free again, once
    T.stream(["x"]).close()


def test_the_cpu_retry_shows_in_server_activity(monkeypatch):
    from core.player import hwaccel
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(1))
    monkeypatch.setattr(T, "_owners", {})
    monkeypatch.setattr(T.subprocess, "Popen",
                        lambda argv, **k: DeadProc() if argv[0] == "gpu" else FakeProc([b"a", b"b"]))
    monkeypatch.setattr(hwaccel, "mark_failed", lambda *a, **k: "NVIDIA GPU stopped working")
    run = T.stream(_gpu_job(), owner="1:tv")
    assert next(run) == b"ab"
    assert T.running("1:tv") == {"encoder": "CPU", "hw_decode": False, "fell_back": "NVIDIA GPU stopped working"}
    from core.server_activity import soulsync as A
    got = A._transcoder({"transcode": {"video": "h264", "audio": "copy", "encoder": "NVIDIA GPU"}},
                        "transcode", "1:tv")
    assert got["hw"] is False and got["fell_back"] == "NVIDIA GPU stopped working"
    run.close()


def test_a_file_that_fails_on_the_cpu_too_is_not_blamed_on_the_gpu(monkeypatch):
    from core.player import hwaccel
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(1))
    monkeypatch.setattr(T, "_owners", {})
    started = []
    monkeypatch.setattr(T.subprocess, "Popen", lambda argv, **k: started.append(argv[0]) or DeadProc())
    monkeypatch.setattr(hwaccel, "mark_failed", lambda *a, **k: pytest.fail("the GPU isn't to blame"))
    assert b"".join(T.stream(_gpu_job())) == b""
    assert started == ["gpu", "cpu"]


def test_no_retry_once_bytes_went_out_or_on_the_cpu(monkeypatch):
    from core.player import hwaccel
    monkeypatch.setattr(T, "_slots", __import__("threading").BoundedSemaphore(2))
    monkeypatch.setattr(T, "_owners", {})
    monkeypatch.setattr(hwaccel, "mark_failed", lambda *a, **k: pytest.fail("no retry expected"))
    big = b"x" * T.PRIME_BYTES

    class LateDeath(FakeProc):
        def __init__(self):
            super().__init__([big])
            self.returncode = 1

    monkeypatch.setattr(T.subprocess, "Popen", lambda argv, **k: LateDeath())
    assert b"".join(T.stream(_gpu_job())) == big
    # a CPU job that fails just ends
    monkeypatch.setattr(T.subprocess, "Popen", lambda argv, **k: DeadProc())
    assert b"".join(T.stream(T.Job(cmd=["cpu"]))) == b""


def test_the_admin_slots_setting_is_the_limit(monkeypatch):
    from core.player import hwaccel
    monkeypatch.setattr(T, "_slots", T._Slots())
    monkeypatch.setattr(T, "_owners", {})
    monkeypatch.setattr(hwaccel, "settings", lambda: {"slots": 1, "gpu": "auto", "device": "auto"})
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: FakeProc([b"ab"]))
    first = T.stream(["ffmpeg"])
    with pytest.raises(T.Busy):
        T.stream(["ffmpeg"])
    monkeypatch.setattr(hwaccel, "settings", lambda: {"slots": 2, "gpu": "auto", "device": "auto"})
    T.stream(["ffmpeg"]).close()  # raised without a restart
    first.close()
    assert T._slots.in_use == 0


def test_build_keeps_a_cpu_twin_only_for_gpu_jobs():
    from core.player import hwaccel
    hevc = T.plan("hevc", "aac", quality="720p")
    job = T.build("ffmpeg", "x.mkv", hevc, 30, hwaccel.make("nvenc"))
    assert job.hw_decode == ("hevc", 8) and job.label == "NVIDIA GPU"
    assert job.cpu_cmd == T.command("ffmpeg", "x.mkv", hevc, 30)
    assert "-hwaccel" not in job.cpu_cmd
    # a copied picture has nothing to fall back from
    assert T.build("ffmpeg", "x", T.plan("h264", "ac3"), 0, hwaccel.make("nvenc")).cpu_cmd is None
    assert T.build("ffmpeg", "x", hevc, 0).cpu_cmd is None
