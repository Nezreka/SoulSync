"""the GPU side of converting video: which hardware this box has, the right
ffmpeg arguments for each kind, and what to do when one breaks.

every backend is free: NVIDIA (nvenc + cuda decode), Intel Quick Sync,
VA-API (Intel and AMD on linux), Apple VideoToolbox, AMD AMF and the Pi's
v4l2m2m encoder. nothing here is behind a paywall.

how a backend is picked:
  * the admin's setting: auto (the first that works), off (the CPU), or one
    named backend
  * a real one-frame encode decides "works": an encoder ffmpeg lists but the
    hardware can't run fails here, not in front of a viewer
  * a backend that fails during a real job is checked again on the spot; if
    the one-frame encode fails too it's marked broken, with ffmpeg's words,
    until the admin tests again or changes the setting. that job retries on
    the CPU, so the viewer only sees a slower start
  * a decode the GPU refused (10-bit h264 on NVIDIA, say) is remembered per
    codec: those files decode on the CPU and still encode on the GPU

the argument builders are plain functions, tested without ffmpeg.
"""

from __future__ import annotations

import glob
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("player.hwaccel")

GPU_MODES = ("auto", "off", "nvenc", "qsv", "vaapi", "videotoolbox", "amf", "v4l2m2m")
SLOTS_DEFAULT = 3
SLOTS_RANGE = (1, 16)
DEFAULT_RENDER = "/dev/dri/renderD128"


class Enc(NamedTuple):
    """an encoder as the command builder needs it. the first four fields are
    the old tuple (encoder, label, input options, filter tail) so callers
    and tests that index it keep working"""
    name: str
    label: str
    pre: List[str]
    tail: str
    key: str = "cpu"
    device: str = ""
    extra: Tuple[str, ...] = ()


CPU = Enc("libx264", "CPU", [], "", "cpu")

# backend key -> (ffmpeg encoder, what to call it)
BACKENDS: Dict[str, Tuple[str, str]] = {
    "nvenc": ("h264_nvenc", "NVIDIA GPU"),
    "qsv": ("h264_qsv", "Intel Quick Sync"),
    "vaapi": ("h264_vaapi", "VA-API GPU"),
    "videotoolbox": ("h264_videotoolbox", "Apple VideoToolbox"),
    "amf": ("h264_amf", "AMD AMF"),
    "v4l2m2m": ("h264_v4l2m2m", "Raspberry Pi encoder"),
}
_BY_ENCODER = {v[0]: k for k, v in BACKENDS.items()}

# what each backend's decoder takes: codec -> the deepest bit depth. 4:2:0
# only (4:2:2 and 4:4:4 decode on the CPU everywhere). NVDEC has no 10-bit
# h264: tested, ffmpeg fails with "Invalid argument" and writes nothing
_DECODE = {
    "nvenc": {"h264": 8, "hevc": 10, "vp9": 10, "av1": 10, "mpeg2video": 8, "vc1": 8},
    "qsv": {"h264": 8, "hevc": 10, "vp9": 10, "av1": 10, "mpeg2video": 8, "vc1": 8},
    "vaapi": {"h264": 8, "hevc": 10, "vp9": 10, "av1": 10, "mpeg2video": 8, "vc1": 8},
    "videotoolbox": {"h264": 8, "hevc": 10},
    "amf": {"h264": 8, "hevc": 10, "vp9": 10, "av1": 10},
    "v4l2m2m": {},
}


def enc_for(t: Any) -> Enc:
    """an Enc from an Enc or the old 4-tuple (a GPU named by its encoder
    gets that backend's own options)"""
    if isinstance(t, Enc):
        return t
    if not t:
        return CPU
    key = _BY_ENCODER.get(t[0])
    if key is None:
        return CPU
    pre = list(t[2])
    device = pre[pre.index("-vaapi_device") + 1] if "-vaapi_device" in pre else ""
    return make(key, device)


def make(key: str, device: str = "", extra: Tuple[str, ...] = ()) -> Enc:
    """the encoder for a backend, with its input options and the filter tail
    that gets CPU frames onto the GPU"""
    if key == "cpu" or key not in BACKENDS:
        return CPU
    name, label = BACKENDS[key]
    if key == "vaapi":
        device = device or DEFAULT_RENDER
        return Enc(name, label, ["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"],
                   ",format=nv12,hwupload", key, device, extra)
    if key == "qsv":
        if sys.platform.startswith("linux"):
            device = device or DEFAULT_RENDER
            pre = ["-init_hw_device", f"vaapi=va:{device}", "-init_hw_device", "qsv=qs@va",
                   "-filter_hw_device", "qs"]
        else:
            pre = ["-init_hw_device", "qsv=qs", "-filter_hw_device", "qs"]
        return Enc(name, label, pre, ",format=nv12,hwupload=extra_hw_frames=64", key, device, extra)
    if key == "v4l2m2m":
        return Enc(name, label, [], ",format=yuv420p", key, "", extra)
    return Enc(name, label, [], "", key, "", extra)


def _cap_bits(cap: Optional[str]) -> Optional[int]:
    """'8M' -> 8000000, '1500k' -> 1500000"""
    if not cap:
        return None
    s = str(cap).strip().lower()
    mult = 1_000_000 if s.endswith("m") else 1000 if s.endswith("k") else 1
    try:
        return int(float(s.rstrip("mk")) * mult)
    except ValueError:
        return None


def _rate(bits: int) -> str:
    return f"{bits // 1000}k"


# what a bitrate-only encoder aims for when the quality has no cap
_DEFAULT_TARGET = {2160: 20_000_000, 1080: 8_000_000, 720: 4_000_000, 480: 1_500_000}


def rate_args(enc: Enc, cap: Optional[str], max_h: int, crf: str = "21") -> List[str]:
    """quality and bitrate for each encoder. a capped quality caps the
    bitrate on every backend (vbr held under maxrate); uncapped is constant
    quality where the encoder has it, else a sane target for the height.

    checked on real hardware (GTX 1080, ffmpeg 8): nvenc -cq with -maxrate
    held a busy 1080p clip at 4.15 Mbps for a 4M cap."""
    bits = _cap_bits(cap)
    key = enc.key
    if key == "cpu":
        out = ["-preset", "veryfast", "-crf", crf, "-profile:v", "high"]
        return out + (["-maxrate", cap, "-bufsize", cap] if cap else [])
    if key == "nvenc":
        # vbr + cq with -b:v 0: quality led, maxrate the ceiling
        out = ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0", "-profile:v", "high"]
        return out + (["-maxrate", cap, "-bufsize", cap] if cap else [])
    target = bits or _DEFAULT_TARGET.get(max_h) or _DEFAULT_TARGET[1080]
    vbr = ["-b:v", _rate(target * 3 // 4), "-maxrate", _rate(target), "-bufsize", _rate(target)]
    if key == "qsv":
        if bits:
            return ["-preset", "veryfast", *vbr, "-profile:v", "high"]
        return ["-preset", "veryfast", "-global_quality", "23", "-look_ahead", "0", "-profile:v", "high"]
    if key == "vaapi":
        # vaapi's default is CQP, which ignores -maxrate: a cap needs VBR said out loud
        lp = list(enc.extra)
        if bits:
            return ["-rc_mode", "VBR", *vbr, *lp, "-profile:v", "high"]
        return ["-rc_mode", "CQP", "-qp", "23", *lp, "-profile:v", "high"]
    if key == "amf":
        if bits:
            return ["-quality", "speed", "-rc", "vbr_peak", *vbr, "-profile:v", "high"]
        return ["-quality", "speed", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23", "-qp_b", "25",
                "-profile:v", "high"]
    if key == "videotoolbox":
        # no bitrate at all and ffmpeg's generic 200 kbps default applies
        return ["-b:v", _rate(target), "-maxrate", _rate(target), "-bufsize", _rate(target * 2),
                "-profile:v", "high", "-realtime", "1"]
    if key == "v4l2m2m":
        return ["-b:v", _rate(target)]
    return []


def can_decode(key: str, codec: Optional[str], bit_depth: Optional[int], pix_fmt: Optional[str] = None) -> bool:
    """this backend decodes this stream on the GPU (and it hasn't failed before)"""
    if key == "cpu" or not codec:
        return False
    depth = _DECODE.get(key, {}).get(str(codec).lower())
    if depth is None or (bit_depth or 8) > depth:
        return False
    if pix_fmt and ("422" in pix_fmt or "444" in pix_fmt):
        return False
    with _lock:
        return (key, str(codec).lower(), bit_depth or 8) not in _decode_broken


def decode_args(enc: Enc, frames_on_gpu: bool) -> List[str]:
    """-hwaccel for this backend. frames_on_gpu: the decoded frames stay on
    the GPU for its own scaler; else ffmpeg copies them back for CPU filters"""
    key = enc.key
    if key == "nvenc":
        return ["-hwaccel", "cuda"] + (["-hwaccel_output_format", "cuda"] if frames_on_gpu else [])
    if key == "vaapi":
        return ["-hwaccel", "vaapi", "-hwaccel_device", "va"] + (
            ["-hwaccel_output_format", "vaapi"] if frames_on_gpu else [])
    if key == "qsv":
        return ["-hwaccel", "qsv", "-hwaccel_device", "qs"] + (
            ["-hwaccel_output_format", "qsv"] if frames_on_gpu else [])
    if key == "videotoolbox":
        return ["-hwaccel", "videotoolbox"]
    if key == "amf" and sys.platform == "win32":
        return ["-hwaccel", "d3d11va"]
    return []


def gpu_scale(enc: Enc, max_h: int) -> Optional[str]:
    """the GPU's own scaler (frames never leave the card), or None"""
    h = f"'min({max_h},ih)'"
    if enc.key == "nvenc":
        return f"scale_cuda=w=-2:h={h}:format=nv12"
    if enc.key == "vaapi":
        return f"scale_vaapi=w=-2:h={h}:format=nv12"
    if enc.key == "qsv":
        return f"scale_qsv=w=-2:h={h}:format=nv12"
    return None


# ── settings ────────────────────────────────────────────────────────────

def settings() -> Dict[str, Any]:
    """the admin's transcoding settings: slots, gpu mode, VA-API device"""
    out = {"slots": SLOTS_DEFAULT, "gpu": "auto", "device": "auto"}
    try:
        from core.settings import config_manager
        raw = config_manager.get("player.transcode", {}) or {}
    except Exception:  # noqa: BLE001 - no config: the defaults
        return out
    if isinstance(raw, dict):
        try:
            out["slots"] = max(SLOTS_RANGE[0], min(SLOTS_RANGE[1], int(raw.get("slots", SLOTS_DEFAULT))))
        except (TypeError, ValueError):
            pass
        if raw.get("gpu") in GPU_MODES:
            out["gpu"] = raw["gpu"]
        dev = str(raw.get("device") or "auto")
        out["device"] = dev if dev == "auto" or dev.startswith("/dev/dri/") else "auto"
    return out


def save_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    """check and keep. raises ValueError with words a person can read"""
    now = settings()
    if "slots" in changes:
        try:
            slots = int(changes["slots"])
        except (TypeError, ValueError):
            raise ValueError("slots is a number") from None
        if not SLOTS_RANGE[0] <= slots <= SLOTS_RANGE[1]:
            raise ValueError(f"slots goes from {SLOTS_RANGE[0]} to {SLOTS_RANGE[1]}")
        now["slots"] = slots
    if "gpu" in changes:
        if changes["gpu"] not in GPU_MODES:
            raise ValueError("gpu is auto, off or a backend")
        now["gpu"] = changes["gpu"]
    if "device" in changes:
        dev = str(changes["device"] or "auto")
        if dev != "auto" and not (dev.startswith("/dev/dri/render") and ".." not in dev):
            raise ValueError("device is auto or a /dev/dri/renderD path")
        now["device"] = dev
    from core.settings import config_manager
    config_manager.set("player.transcode", now)
    forget()
    return now


# ── detection and what broke ────────────────────────────────────────────

_lock = threading.Lock()
_chosen: Optional[Enc] = None
_checked = False
_results: Dict[str, Dict[str, Any]] = {}
_broken: Dict[str, str] = {}
_decode_broken: Dict[Tuple[str, str, int], str] = {}
_last_fallback: Optional[Dict[str, Any]] = None


def forget() -> None:
    """a setting changed or the admin pressed test: find the GPU again, and
    give anything marked broken another go"""
    global _checked, _chosen, _last_fallback
    with _lock:
        _checked = False
        _chosen = None
        _results.clear()
        _broken.clear()
        _decode_broken.clear()
        _last_fallback = None


def render_devices() -> List[str]:
    return sorted(glob.glob("/dev/dri/renderD*"))


def candidates(mode: str, device: str = "auto") -> List[Enc]:
    """what to try, in order, on this platform"""
    if mode == "off":
        return []
    linux = sys.platform.startswith("linux")
    arm = platform.machine().lower() in ("aarch64", "arm64", "armv7l", "armv8l")
    devices = [device] if device != "auto" else (render_devices() or [DEFAULT_RENDER])
    order: List[Enc] = []
    for key in ("nvenc", "qsv", "vaapi", "videotoolbox", "amf", "v4l2m2m"):
        if mode not in ("auto", key):
            continue
        if key == "videotoolbox" and sys.platform != "darwin" and mode == "auto":
            continue
        if key == "v4l2m2m" and not (linux and arm) and mode == "auto":
            continue
        if key in ("vaapi", "qsv") and linux:
            order += [make(key, d) for d in devices]
            if key == "vaapi":
                # newer low power Intel (N100 and the like) only has the low power encoder
                order += [make(key, d, ("-low_power", "1")) for d in devices]
        elif key == "vaapi":
            continue
        else:
            order.append(make(key))
    return order


def try_encode(ffmpeg: str, enc: Enc) -> Tuple[bool, str]:
    """a real one-frame encode through the same filter tail a job uses.
    (works, ffmpeg's last words)"""
    vf = "format=yuv420p" + enc.tail
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *enc.pre,
           "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.1",
           "-vf", vf, "-frames:v", "1", "-c:v", enc.name, *enc.extra, "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,  # noqa: S603 - our own argv
                           timeout=20)
    except subprocess.TimeoutExpired:
        return False, "the test encode took longer than 20 seconds"
    except Exception as e:  # noqa: BLE001 - not there, not working
        return False, str(e)[:200]
    if p.returncode == 0:
        return True, ""
    return False, _words(p.stderr)


def _words(err: bytes) -> str:
    """the useful end of ffmpeg's complaint, short enough to show"""
    text = (err or b"").decode("utf-8", "replace").strip().splitlines()
    lines = [ln.strip() for ln in text if ln.strip()]
    words = ("error", "fail", "not ", "invalid", "cannot", "unable", "no ", "unsupported", "impossible")
    keep = [ln for ln in lines if any(w in ln.lower() for w in words)]
    return " / ".join((keep or lines)[-3:])[:300]


def detect(ffmpeg: str, tester=None) -> Enc:
    """the encoder to use (found once per settings change). the CPU's when
    nothing works or the GPU is off"""
    global _chosen, _checked
    tester = tester or try_encode
    with _lock:
        if _checked:
            return _chosen or CPU
    conf = settings()
    found: Optional[Enc] = None
    results: Dict[str, Dict[str, Any]] = {}
    for enc in candidates(conf["gpu"], conf["device"]):
        with _lock:
            if enc.key in _broken:
                results.setdefault(enc.key, {"label": enc.label, "works": False, "reason": _broken[enc.key]})
                continue
        ok, why = tester(ffmpeg, enc)
        prev = results.get(enc.key)
        if ok or not prev or not prev["works"]:
            results[enc.key] = {"label": enc.label, "works": ok, "reason": why,
                                "device": enc.device or None, "low_power": bool(enc.extra)}
        if ok:
            found = enc
            break
    with _lock:
        _results.clear()
        _results.update(results)
        _chosen = found
        _checked = True
    if found:
        logger.info("player transcoding on %s%s", found.label, f" ({found.device})" if found.device else "")
    else:
        logger.info("player transcoding on the CPU (libx264)%s", "" if conf["gpu"] != "off" else ": GPU off")
    return found or CPU


def chosen() -> Optional[Enc]:
    with _lock:
        return _chosen if _checked else None


def mark_failed(ffmpeg: str, enc: Enc, decoded: Optional[Tuple[str, int]], reason: str,
                tester=None) -> str:
    """a GPU job failed before it wrote anything. a GPU decode that failed is
    remembered for that codec; otherwise the encoder is tested again and
    marked broken when that fails too. what happened, in words"""
    global _last_fallback, _chosen, _checked
    tester = tester or try_encode
    reason = (reason or "ffmpeg stopped without saying why")[:300]
    if decoded is not None:
        codec, depth = decoded
        with _lock:
            _decode_broken[(enc.key, codec, depth)] = reason
            what = f"{enc.label} couldn't decode {depth}-bit {codec.upper()}; those decode on the CPU now"
            _last_fallback = {"at": time.time(), "backend": enc.key, "what": what, "reason": reason}
        logger.warning("%s: %s", what, reason)
        return what
    ok, why = tester(ffmpeg, enc)
    with _lock:
        if ok:
            what = f"{enc.label} failed one job (it still passes a test encode), so it ran on the CPU"
        else:
            _broken[enc.key] = why or reason
            if _chosen is not None and _chosen.key == enc.key:
                _chosen, _checked = None, False
            what = f"{enc.label} stopped working and is off until it's tested again"
        _last_fallback = {"at": time.time(), "backend": enc.key, "what": what, "reason": reason}
    logger.warning("%s: %s", what, reason)
    return what


def status(ffmpeg: Optional[str]) -> Dict[str, Any]:
    """what settings shows: the setting, what's in use, what was tried"""
    conf = settings()
    enc = detect(ffmpeg) if ffmpeg else CPU
    with _lock:
        return {
            **conf,
            "slots_range": list(SLOTS_RANGE),
            "modes": list(GPU_MODES),
            "ffmpeg": bool(ffmpeg),
            "using": {"key": enc.key, "label": enc.label, "device": enc.device or None},
            "tried": [{"key": k, **v} for k, v in _results.items()],
            "broken": [{"key": k, "label": BACKENDS.get(k, (k, k))[1], "reason": r} for k, r in _broken.items()],
            "decode_cpu": [{"key": k, "codec": c, "bit_depth": d, "reason": r}
                           for (k, c, d), r in _decode_broken.items()],
            "last_fallback": dict(_last_fallback) if _last_fallback else None,
            "devices": render_devices(),
        }


def full_test(ffmpeg: str, enc: Enc, timeout: float = 60) -> Dict[str, Any]:
    """the whole pipeline the player runs, on a real clip: a 1 second
    1080p h264 file decoded, scaled to 720p and encoded with this backend,
    through the same command builder the player uses"""
    from core.player import transcode as T
    with tempfile.TemporaryDirectory(prefix="ss_gputest_") as tmp:
        clip = os.path.join(tmp, "clip.mkv")
        made = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",  # noqa: S603
                               "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=1",
                               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", clip],
                              capture_output=True, timeout=timeout)
        if made.returncode != 0:
            return {"ok": False, "reason": "couldn't make a test clip: " + _words(made.stderr)}
        p = T.plan("hevc", "aac", quality="720p", height=1080, codec_facts={"codec": "h264", "bit_depth": 8})
        cmd = T.command(ffmpeg, clip, p, 0, enc)
        cmd = cmd[:cmd.index("-movflags")] + ["-f", "null", "-"]
        started = time.monotonic()
        run = subprocess.run(cmd, capture_output=True, timeout=timeout)  # noqa: S603 - our own argv
        took = round(time.monotonic() - started, 2)
        hw = "-hwaccel" in cmd
        if run.returncode != 0:
            return {"ok": False, "hw_decode": hw, "seconds": took, "reason": _words(run.stderr)}
        return {"ok": True, "hw_decode": hw, "seconds": took, "reason": ""}
