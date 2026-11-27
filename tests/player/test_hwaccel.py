"""the GPU side of converting: the exact ffmpeg arguments for every backend
(golden lists), when the GPU decodes, how a backend is picked, what happens
when one breaks, and the admin's settings."""

from __future__ import annotations

import pytest
from flask import Flask

from core.player import hwaccel as H
from core.player import transcode as T

HEAD = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
TAIL_COPY_AUDIO = ["-c:a", "copy", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
GOP = ["-g", "48", "-keyint_min", "48", "-sc_threshold", "0"]
MAP = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
# the real settings reader, before the fixture below stands in for it
REAL_SETTINGS = H.settings


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(H, "settings", lambda: {"slots": 3, "gpu": "auto", "device": "auto"})
    H.forget()
    yield
    H.forget()


def hevc10(quality="720p", **kw):
    return T.plan("hevc", "aac", quality=quality, height=2160, codec_facts={"bit_depth": 10}, **kw)


# ── golden commands, one per backend ────────────────────────────────────

def test_nvenc_decodes_scales_and_encodes_on_the_card():
    cmd = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("nvenc"))
    assert cmd == HEAD + ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", "in.mkv", *MAP,
                          "-vf", "scale_cuda=w=-2:h='min(720,ih)':format=nv12", "-c:v", "h264_nvenc",
                          "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                          "-profile:v", "high", "-maxrate", "4M", "-bufsize", "4M", *GOP, *TAIL_COPY_AUDIO]
    # original: quality led, no ceiling
    orig = T.command("ffmpeg", "in.mkv", hevc10("original"), 0, H.make("nvenc"))
    assert "-maxrate" not in orig and orig[orig.index("-b:v") + 1] == "0"


def test_vaapi_caps_with_vbr_and_uses_cqp_uncapped():
    cmd = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("vaapi", "/dev/dri/renderD128"))
    assert cmd == HEAD + ["-init_hw_device", "vaapi=va:/dev/dri/renderD128", "-filter_hw_device", "va",
                          "-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi",
                          "-i", "in.mkv", *MAP, "-vf", "scale_vaapi=w=-2:h='min(720,ih)':format=nv12",
                          "-c:v", "h264_vaapi", "-rc_mode", "VBR", "-b:v", "3000k", "-maxrate", "4000k",
                          "-bufsize", "4000k", "-profile:v", "high", *GOP, *TAIL_COPY_AUDIO]
    orig = T.command("ffmpeg", "in.mkv", hevc10("original"), 0, H.make("vaapi", "/dev/dri/renderD128"))
    assert orig[orig.index("-rc_mode"):orig.index("-profile:v")] == ["-rc_mode", "CQP", "-qp", "23"]
    # the low power encoder (N100 class Intel) says so
    lp = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("vaapi", "/dev/dri/renderD128", ("-low_power", "1")))
    assert lp[lp.index("-low_power") + 1] == "1"


def test_qsv_on_linux_derives_from_vaapi(monkeypatch):
    monkeypatch.setattr(H.sys, "platform", "linux")
    cmd = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("qsv", "/dev/dri/renderD128"))
    assert cmd == HEAD + ["-init_hw_device", "vaapi=va:/dev/dri/renderD128", "-init_hw_device", "qsv=qs@va",
                          "-filter_hw_device", "qs", "-hwaccel", "qsv", "-hwaccel_device", "qs",
                          "-hwaccel_output_format", "qsv", "-i", "in.mkv", *MAP,
                          "-vf", "scale_qsv=w=-2:h='min(720,ih)':format=nv12", "-c:v", "h264_qsv",
                          "-preset", "veryfast", "-b:v", "3000k", "-maxrate", "4000k", "-bufsize", "4000k",
                          "-profile:v", "high", *GOP, *TAIL_COPY_AUDIO]
    orig = T.command("ffmpeg", "in.mkv", hevc10("original"), 0, H.make("qsv", "/dev/dri/renderD128"))
    assert "-global_quality" in orig and "-maxrate" not in orig


def test_videotoolbox_always_gets_a_bitrate():
    """no -b:v and ffmpeg's generic 200 kbps applies: a Mac's conversions looked awful"""
    cmd = T.command("ffmpeg", "in.mkv", hevc10("original"), 0, H.make("videotoolbox"))
    assert cmd == HEAD + ["-hwaccel", "videotoolbox", "-i", "in.mkv", *MAP,
                          "-vf", "scale=-2:'min(1080,ih)',format=yuv420p", "-c:v", "h264_videotoolbox",
                          "-b:v", "8000k", "-maxrate", "8000k", "-bufsize", "16000k", "-profile:v", "high",
                          "-realtime", "1", *GOP, *TAIL_COPY_AUDIO]
    capped = T.command("ffmpeg", "in.mkv", hevc10("480p"), 0, H.make("videotoolbox"))
    assert capped[capped.index("-b:v") + 1] == "1500k"


def test_amf_and_the_pi():
    amf = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("amf"))
    assert amf[amf.index("-c:v"):amf.index("-g")] == [
        "-c:v", "h264_amf", "-quality", "speed", "-rc", "vbr_peak", "-b:v", "3000k", "-maxrate", "4000k",
        "-bufsize", "4000k", "-profile:v", "high"]
    pi = T.command("ffmpeg", "in.mkv", hevc10(), 0, H.make("v4l2m2m"))
    # the pi decodes on the CPU and hands the encoder plain yuv420p
    assert "-hwaccel" not in pi
    assert pi[pi.index("-vf") + 1] == "scale=-2:'min(720,ih)',format=yuv420p,format=yuv420p"
    assert pi[pi.index("-c:v") + 1:pi.index("-g")] == ["h264_v4l2m2m", "-b:v", "4000k"]


# ── when the GPU decodes ────────────────────────────────────────────────

def test_what_each_gpu_decodes():
    assert H.can_decode("nvenc", "hevc", 10)
    # NVDEC has no 10-bit h264 (real GTX 1080: ffmpeg fails, writes nothing)
    assert not H.can_decode("nvenc", "h264", 10)
    assert not H.can_decode("vaapi", "h264", 8, "yuv422p")
    assert not H.can_decode("v4l2m2m", "h264", 8)
    assert not H.can_decode("cpu", "h264", 8)
    assert not H.can_decode("nvenc", "mpeg4", 8)
    hi10 = T.plan("h264", "aac", codec_facts={"bit_depth": 10})
    cmd = T.command("ffmpeg", "x", hi10, 0, H.make("nvenc"))
    assert "-hwaccel" not in cmd and cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:'min(1080,ih)',format=yuv420p"


def test_tonemap_and_burn_in_decode_on_the_gpu_and_filter_on_the_cpu():
    hdr = T.plan("hevc", "aac", hdr="HDR10", codec_facts={"bit_depth": 10})
    cmd = T.command("ffmpeg", "x", hdr, 0, H.make("nvenc"), can_tonemap=True)
    # frames come back to the CPU for zscale (no -hwaccel_output_format)
    assert cmd[5:7] == ["-hwaccel", "cuda"] and "-hwaccel_output_format" not in cmd
    assert "tonemap=hable" in cmd[cmd.index("-vf") + 1]
    burn = T.plan("hevc", "aac", burn=0)
    vcmd = T.command("ffmpeg", "x", burn, 0, H.make("vaapi"))
    assert "-hwaccel_output_format" not in vcmd
    assert vcmd[vcmd.index("-filter_complex") + 1].endswith(",format=yuv420p,format=nv12,hwupload[v]")


def test_a_decode_that_failed_is_remembered_per_codec():
    tested = []
    what = H.mark_failed("ffmpeg", H.make("nvenc"), ("av1", 10), "No decoder surfaces",
                         tester=lambda *a: tested.append(a) or (True, ""))
    assert "10-bit AV1" in what and tested == []
    assert not H.can_decode("nvenc", "av1", 10) and H.can_decode("nvenc", "hevc", 10)
    cmd = T.command("ffmpeg", "x", T.plan("av1", "aac", quality="720p", codec_facts={"bit_depth": 10}), 0,
                    H.make("nvenc"))
    assert "-hwaccel" not in cmd and "h264_nvenc" in cmd
    assert H.status(None)["decode_cpu"] == [{"key": "nvenc", "codec": "av1", "bit_depth": 10,
                                             "reason": "No decoder surfaces"}]


# ── picking a backend ───────────────────────────────────────────────────

def _linux(monkeypatch, devices=("/dev/dri/renderD128",), arm=False):
    monkeypatch.setattr(H.sys, "platform", "linux")
    monkeypatch.setattr(H.platform, "machine", lambda: "aarch64" if arm else "x86_64")
    monkeypatch.setattr(H, "render_devices", lambda: list(devices))


def test_auto_tries_every_free_backend_this_box_could_have(monkeypatch):
    _linux(monkeypatch, devices=("/dev/dri/renderD128", "/dev/dri/renderD129"))
    order = [(e.key, e.device, e.extra) for e in H.candidates("auto")]
    assert order == [("nvenc", "", ()), ("qsv", "/dev/dri/renderD128", ()), ("qsv", "/dev/dri/renderD129", ()),
                     ("vaapi", "/dev/dri/renderD128", ()), ("vaapi", "/dev/dri/renderD129", ()),
                     ("vaapi", "/dev/dri/renderD128", ("-low_power", "1")),
                     ("vaapi", "/dev/dri/renderD129", ("-low_power", "1")), ("amf", "", ())]
    assert [e.key for e in H.candidates("auto", "/dev/dri/renderD129") if e.key == "vaapi"] == ["vaapi", "vaapi"]
    _linux(monkeypatch, arm=True)
    assert "v4l2m2m" in [e.key for e in H.candidates("auto")]
    assert H.candidates("off") == []
    monkeypatch.setattr(H.sys, "platform", "darwin")
    assert [e.key for e in H.candidates("auto")] == ["nvenc", "qsv", "videotoolbox", "amf"]


def test_the_second_gpu_wins_when_the_first_can_not(monkeypatch):
    _linux(monkeypatch, devices=("/dev/dri/renderD128", "/dev/dri/renderD129"))
    enc = H.detect("ffmpeg", tester=lambda ff, e: (e.key == "vaapi" and e.device.endswith("129"), "no"))
    assert (enc.key, enc.device) == ("vaapi", "/dev/dri/renderD129")
    st = H.status("ffmpeg")
    assert st["using"] == {"key": "vaapi", "label": "VA-API GPU", "device": "/dev/dri/renderD129"}
    assert {t["key"]: t["works"] for t in st["tried"]} == {"nvenc": False, "qsv": False, "vaapi": True}


def test_off_is_the_cpu_and_a_named_gpu_that_fails_says_why(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(H, "settings", lambda: {"slots": 3, "gpu": "off", "device": "auto"})
    assert H.detect("ffmpeg", tester=lambda *a: pytest.fail("nothing is tried")) is H.CPU
    H.forget()
    monkeypatch.setattr(H, "settings", lambda: {"slots": 3, "gpu": "nvenc", "device": "auto"})
    assert H.detect("ffmpeg", tester=lambda ff, e: (False, "Cannot load libcuda.so.1")) is H.CPU
    assert H.status("ffmpeg")["tried"] == [{"key": "nvenc", "label": "NVIDIA GPU", "works": False,
                                            "reason": "Cannot load libcuda.so.1", "device": None,
                                            "low_power": False}]


def test_a_gpu_that_stops_working_is_off_until_tested_again(monkeypatch):
    _linux(monkeypatch)
    alive = {"nvenc": True, "vaapi": True}
    tester = lambda ff, e: (alive.get(e.key, False), "" if alive.get(e.key) else "OpenEncodeSessionEx failed")  # noqa: E731
    assert H.detect("ffmpeg", tester=tester).key == "nvenc"
    # one bad job, but a test encode still works: no mark, just that job on the CPU
    what = H.mark_failed("ffmpeg", H.make("nvenc"), None, "session limit", tester=tester)
    assert "still passes" in what and H.detect("ffmpeg", tester=tester).key == "nvenc"
    alive["nvenc"] = False
    what = H.mark_failed("ffmpeg", H.make("nvenc"), None, "CUDA_ERROR_NO_DEVICE", tester=tester)
    assert "off until it's tested again" in what
    st = H.status(None)
    assert st["broken"] == [{"key": "nvenc", "label": "NVIDIA GPU", "reason": "OpenEncodeSessionEx failed"}]
    assert st["last_fallback"]["reason"] == "CUDA_ERROR_NO_DEVICE"
    # the next pick skips it, even once it would pass again
    alive["nvenc"] = True
    assert H.detect("ffmpeg", tester=tester).key == "vaapi"
    H.forget()  # the admin's test button
    assert H.detect("ffmpeg", tester=tester).key == "nvenc"


def test_the_test_encode_runs_the_same_tail_a_job_uses(monkeypatch):
    seen = []

    class Done:
        returncode = 1
        stderr = b"[h264_vaapi @ 0x1] No usable encoding entrypoint found\nError while opening encoder\n"

    monkeypatch.setattr(H.subprocess, "run", lambda cmd, **k: seen.append(cmd) or Done())
    ok, why = H.try_encode("ffmpeg", H.make("vaapi", "/dev/dri/renderD128", ("-low_power", "1")))
    assert not ok and "No usable encoding entrypoint" in why
    cmd = seen[0]
    assert cmd[cmd.index("-vf") + 1] == "format=yuv420p,format=nv12,hwupload"
    assert cmd[cmd.index("-c:v") + 1:cmd.index("-c:v") + 4] == ["h264_vaapi", "-low_power", "1"]


# ── settings ────────────────────────────────────────────────────────────

class FakeConfig:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


@pytest.fixture
def config(monkeypatch):
    import core.settings as cs
    fake = FakeConfig()
    monkeypatch.setattr(cs, "config_manager", fake)
    monkeypatch.setattr(H, "settings", REAL_SETTINGS)
    return fake


def test_settings_are_checked_and_kept(config):
    assert H.settings() == {"slots": 3, "gpu": "auto", "device": "auto"}
    assert H.save_settings({"slots": 6, "gpu": "vaapi", "device": "/dev/dri/renderD129"}) == {
        "slots": 6, "gpu": "vaapi", "device": "/dev/dri/renderD129"}
    assert config.data["player.transcode"]["slots"] == 6
    for bad in ({"slots": 0}, {"slots": 99}, {"slots": "x"}, {"gpu": "cuda"}, {"device": "/etc/passwd"},
                {"device": "/dev/dri/../../etc"}):
        with pytest.raises(ValueError):
            H.save_settings(bad)
    # junk in the config reads as the defaults
    config.data["player.transcode"] = {"slots": "lots", "gpu": "quantum", "device": "/tmp/x"}
    assert H.settings() == {"slots": 3, "gpu": "auto", "device": "auto"}


def test_changing_a_setting_finds_the_gpu_again(config, monkeypatch):
    monkeypatch.setattr(H, "candidates", lambda mode, device="auto": [H.make("nvenc")])
    assert H.detect("ffmpeg", tester=lambda *a: (True, "")).key == "nvenc"
    H.save_settings({"gpu": "off"})
    assert H.chosen() is None and H._checked is False


# ── the admin routes ────────────────────────────────────────────────────

@pytest.fixture
def admin_client(monkeypatch, config):
    import api.player_stream as S
    fresh = H
    monkeypatch.setattr(T, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(fresh, "try_encode", lambda ff, e: (e.key == "nvenc", "" if e.key == "nvenc" else "no"))
    monkeypatch.setattr(fresh, "candidates", lambda mode, device="auto": [] if mode == "off" else [fresh.make("nvenc")])
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(S.create_blueprint())
    return app.test_client(), fresh


def test_the_converting_settings_routes(admin_client, monkeypatch):
    c, fresh = admin_client
    got = c.get("/api/player/converting/settings").get_json()
    assert got["success"] and got["using"]["label"] == "NVIDIA GPU" and got["slots"] == 3
    assert got["modes"][:2] == ["auto", "off"]
    put = c.put("/api/player/converting/settings", json={"gpu": "off", "slots": 5}).get_json()
    assert put["using"]["key"] == "cpu" and put["slots"] == 5
    assert c.put("/api/player/converting/settings", json={"slots": 50}).status_code == 400
    assert c.put("/api/player/converting/settings", json=[]).status_code == 400


def test_the_gpu_test_button_runs_a_real_pipeline(admin_client, monkeypatch):
    c, fresh = admin_client
    runs = []

    def full(ff, enc, timeout=60):
        runs.append(enc.key)
        return {"ok": len(runs) > 1, "hw_decode": True, "seconds": 0.4,
                "reason": "" if len(runs) > 1 else "Impossible to convert between the formats"}

    monkeypatch.setattr(fresh, "full_test", full)
    body = c.post("/api/player/converting/test").get_json()
    # the decode failed, so the card encodes what the CPU decodes
    assert body["test"]["ok"] and body["test"]["note"].startswith("Decoding on the CPU")
    assert body["decode_cpu"][0]["codec"] == "h264"
    monkeypatch.setattr(fresh, "full_test", lambda ff, enc, timeout=60: {"ok": False, "hw_decode": False,
                                                                       "reason": "Cannot init CUDA"})
    body = c.post("/api/player/converting/test").get_json()
    assert not body["test"]["ok"] and body["broken"][0]["reason"] == "Cannot init CUDA"
    assert body["using"]["key"] == "cpu"


def test_only_an_admin_changes_them(monkeypatch, config):
    import api.player_stream as S
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: False)
    app = Flask(__name__)
    app.register_blueprint(S.create_blueprint())
    c = app.test_client()
    assert c.get("/api/player/converting/settings").status_code == 403
    assert c.put("/api/player/converting/settings", json={"slots": 2}).status_code == 403
    assert c.post("/api/player/converting/test").status_code == 403


# ── offline copies ──────────────────────────────────────────────────────

def test_an_offline_copy_retries_on_the_cpu_and_only_then_blames_the_gpu(monkeypatch, tmp_path):
    from core.player import offline_jobs as J
    monkeypatch.setattr(T, "encoder", lambda ff: H.make("nvenc"))
    monkeypatch.setattr(T, "has_filter", lambda ff, name: False)
    runs, marked = [], []

    def fake(cmd, duration, progress):
        runs.append("h264_nvenc" in cmd)
        if "h264_nvenc" in cmd:
            raise RuntimeError("[h264_nvenc] No capable devices found")

    monkeypatch.setattr(J, "_ffmpeg", fake)
    monkeypatch.setattr(H, "mark_failed", lambda ff, enc, dec, reason, tester=None: marked.append((enc.key, reason)))
    job = J.Job(id="t", kind="convert", media="video", quality="phone", out=str(tmp_path / "o.mp4"),
                item_type="movie", item_id="1", input="in.mkv", duration=3.0,
                video={"video_codec": "hevc", "audio_codec": "ac3", "height": 1080})
    J._convert_video("ffmpeg", job, str(tmp_path / "o.part"), lambda f: None)
    assert runs == [True, False] and marked == [("nvenc", "[h264_nvenc] No capable devices found")]
    # the CPU failing too: nobody's marked, the copy fails as before
    marked.clear()
    monkeypatch.setattr(J, "_ffmpeg", lambda *a: (_ for _ in ()).throw(RuntimeError("Invalid data found")))
    with pytest.raises(RuntimeError):
        J._convert_video("ffmpeg", job, str(tmp_path / "o.part"), lambda f: None)
    assert marked == []
