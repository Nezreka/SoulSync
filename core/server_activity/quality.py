"""quality labels both servers share: 4K, HEVC Main 10 · HDR10, TrueHD Atmos 7.1,
FLAC 24-bit · 96 kHz. pure, so plex and jellyfin cards read the same."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_CODECS = {
    "hevc": "HEVC", "h265": "HEVC", "x265": "HEVC", "h264": "H.264", "avc": "H.264", "avc1": "H.264",
    "x264": "H.264", "av1": "AV1", "vp9": "VP9", "vp8": "VP8", "mpeg2video": "MPEG-2", "mpeg2": "MPEG-2",
    "mpeg4": "MPEG-4", "vc1": "VC-1", "wmv3": "WMV", "msmpeg4v3": "DivX", "mjpeg": "MJPEG",
    "truehd": "TrueHD", "eac3": "EAC3", "ac3": "AC3", "dca": "DTS", "dts": "DTS", "aac": "AAC",
    "flac": "FLAC", "mp3": "MP3", "alac": "ALAC", "opus": "Opus", "vorbis": "Vorbis", "wma": "WMA",
    "wmapro": "WMA Pro", "ape": "APE", "wavpack": "WavPack", "dsd": "DSD", "mp2": "MP2",
    "pcm": "PCM", "lpcm": "PCM", "pcm_s16le": "PCM", "pcm_s24le": "PCM", "pcm_s32le": "PCM",
    "srt": "SRT", "subrip": "SRT", "ass": "ASS", "ssa": "SSA", "pgs": "PGS", "pgssub": "PGS",
    "hdmv_pgs_subtitle": "PGS", "vobsub": "VobSub", "dvd_subtitle": "VobSub", "dvdsub": "VobSub",
    "dvb_subtitle": "DVB", "dvbsub": "DVB", "mov_text": "MOV text", "webvtt": "WebVTT", "vtt": "WebVTT",
    "eia_608": "CC", "smi": "SMI", "ttml": "TTML",
}

_CONTAINERS = {
    "mkv": "MKV", "matroska": "MKV", "mp4": "MP4", "m4v": "MP4", "mov": "MOV", "avi": "AVI",
    "mpegts": "MPEG-TS", "ts": "MPEG-TS", "m2ts": "M2TS", "hls": "HLS", "dash": "DASH", "webm": "WebM",
    "m4b": "M4B", "m4a": "M4A", "mp3": "MP3", "flac": "FLAC", "ogg": "OGG", "opus": "OPUS",
    "wav": "WAV", "aac": "AAC", "wma": "WMA", "wmv": "WMV", "ape": "APE", "alac": "ALAC",
}

LOSSLESS = {"flac", "alac", "ape", "wavpack", "pcm", "lpcm", "dsd", "wav", "truehd", "mlp"}
IMAGE_SUBTITLES = {"pgs", "pgssub", "hdmv_pgs_subtitle", "vobsub", "dvd_subtitle", "dvdsub",
                   "dvb_subtitle", "dvbsub"}
STYLED_SUBTITLES = {"ass", "ssa"}


def codec_label(codec: Any, profile: Any = None) -> str:
    c = str(codec or "").strip().lower()
    if not c:
        return ""
    p = str(profile or "").strip().lower()
    if c in ("dca", "dts", "dca-ma", "dts-hd"):
        if c == "dca-ma" or p in ("ma", "dts-hd ma") or "hd ma" in p:
            return "DTS-HD MA"
        if p in ("hra", "dts-hd hra") or "hra" in p:
            return "DTS-HD HRA"
        if "x" in p and "dts" in p:
            return "DTS:X"
        return "DTS"
    if c.startswith("pcm"):
        return "PCM"
    return _CODECS.get(c, c.upper())


def container_label(container: Any) -> str:
    c = str(container or "").strip().lower()
    if not c:
        return ""
    c = c.split(",")[0]
    return _CONTAINERS.get(c, c.upper())


def res_label(resolution: Any = None, height: Any = None, width: Any = None) -> str:
    """4K / 1080p / 720p / 576p / 480p / SD. plex says '4k', '1080', 'sd';
    jellyfin gives a height."""
    r = str(resolution or "").strip().lower()
    if r in ("4k", "2160", "2160p", "uhd"):
        return "4K"
    if r in ("8k", "4320"):
        return "8K"
    if r == "sd":
        return "SD"
    m = re.match(r"^(\d{3,4})p?$", r)
    if m:
        return m.group(1) + "p"
    try:
        h = int(height or 0)
        w = int(width or 0)
    except (TypeError, ValueError):
        h, w = 0, 0
    # widescreen films are short: a 3840x1600 file is still 4K
    if h >= 1900 or w >= 3400:
        return "4K"
    if h >= 900 or w >= 1700:
        return "1080p"
    if h >= 650 or w >= 1200:
        return "720p"
    if h >= 540:
        return "576p"
    if h >= 400:
        return "480p"
    if h > 0:
        return "SD"
    return ""


def hdr_label(color_trc: Any = None, dovi: Any = False, range_type: Any = None,
              hdr10_plus: Any = False) -> str:
    """HDR10 / HDR10+ / Dolby Vision / HLG, or '' for SDR / unknown."""
    rt = str(range_type or "").strip().lower()
    if rt:
        if rt.startswith("dovi"):
            return "Dolby Vision"
        if rt == "hdr10plus":
            return "HDR10+"
        if rt == "hdr10":
            return "HDR10"
        if rt == "hlg":
            return "HLG"
        if rt == "sdr":
            return ""
    if str(dovi or "").lower() in ("1", "true"):
        return "Dolby Vision"
    trc = str(color_trc or "").strip().lower()
    if trc in ("smpte2084", "pq"):
        return "HDR10+" if str(hdr10_plus or "").lower() in ("1", "true") else "HDR10"
    if trc in ("arib-std-b67", "hlg"):
        return "HLG"
    return ""


def channels_label(channels: Any = None, layout: Any = None) -> str:
    lay = str(layout or "").strip().lower()
    m = re.match(r"^(\d)\.(\d)", lay)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    if lay in ("stereo",):
        return "Stereo"
    if lay in ("mono",):
        return "Mono"
    try:
        n = int(channels or 0)
    except (TypeError, ValueError):
        return ""
    return {1: "Mono", 2: "Stereo", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}.get(n, f"{n} ch" if n else "")


def is_lossless(codec: Any) -> bool:
    c = str(codec or "").strip().lower()
    return c in LOSSLESS or c.startswith("pcm")


def is_hires(bit_depth: Any, sample_rate: Any) -> bool:
    try:
        return int(bit_depth or 0) > 16 or int(sample_rate or 0) > 48000
    except (TypeError, ValueError):
        return False


def khz(sample_rate: Any) -> str:
    try:
        hz = int(sample_rate or 0)
    except (TypeError, ValueError):
        return ""
    if not hz:
        return ""
    k = hz / 1000.0
    return (f"{k:.1f}".rstrip("0").rstrip(".")) + " kHz"


def kbps_label(kbps: Any) -> str:
    try:
        k = float(kbps or 0)
    except (TypeError, ValueError):
        return ""
    if k <= 0:
        return ""
    if k >= 1000:
        return f"{k / 1000:.1f} Mbps"
    return f"{round(k)} kbps"


def _int(v: Any) -> Optional[int]:
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def video_side(*, codec: Any = None, profile: Any = None, width: Any = None, height: Any = None,
               resolution: Any = None, bit_depth: Any = None, hdr: str = "",
               bitrate_kbps: Any = None, frame_rate: Any = None) -> Dict[str, Any]:
    res = res_label(resolution, height, width)
    c = codec_label(codec)
    prof = str(profile or "").strip()
    prof = " ".join(w.capitalize() if w.isalpha() else w for w in prof.split()) if prof else ""
    head = " ".join(x for x in (res, c, prof if c in ("HEVC", "H.264", "AV1") else "") if x)
    label = head + (" · " + hdr if hdr else (" · SDR" if res else ""))
    return {"codec": c, "resolution": res, "width": _int(width), "height": _int(height),
            "profile": prof or None, "bit_depth": _int(bit_depth), "hdr": hdr or None,
            "bitrate_kbps": _int(bitrate_kbps), "frame_rate": str(frame_rate or "") or None,
            "label": label.strip()}


def audio_side(*, codec: Any = None, profile: Any = None, channels: Any = None, layout: Any = None,
               language: Any = None, bit_depth: Any = None, sample_rate: Any = None,
               bitrate_kbps: Any = None, atmos: bool = False) -> Dict[str, Any]:
    c = codec_label(codec, profile)
    ch = channels_label(channels, layout)
    lang = str(language or "").strip()
    lossless = is_lossless(codec)
    if lossless and (bit_depth or sample_rate):
        q = " · ".join(x for x in (f"{_int(bit_depth)}-bit" if _int(bit_depth) else "", khz(sample_rate)) if x)
        quality = f"{c} {q}".strip()
    elif not lossless and _int(bitrate_kbps):
        quality = f"{c} {kbps_label(bitrate_kbps)}".strip()
    else:
        quality = c
    label = " ".join(x for x in (c, "Atmos" if atmos else "", ch) if x)
    if lang:
        label += " · " + lang
    return {"codec": c, "channels": _int(channels), "channel_layout": ch or None,
            "language": lang or None, "bit_depth": _int(bit_depth), "sample_rate_hz": _int(sample_rate),
            "bitrate_kbps": _int(bitrate_kbps), "atmos": bool(atmos), "lossless": lossless,
            "hires": bool(lossless and is_hires(bit_depth, sample_rate)),
            "quality": quality, "label": label}


def subtitle_side(*, codec: Any = None, language: Any = None, title: Any = None,
                  forced: Any = False) -> Dict[str, Any]:
    c = codec_label(codec)
    lang = str(language or "").strip() or str(title or "").strip()
    low = str(codec or "").lower()
    kind = "image" if low in IMAGE_SUBTITLES else ("styled" if low in STYLED_SUBTITLES else "text")
    label = (lang + (f" ({c})" if c else "")).strip() or c
    if str(forced or "").lower() in ("1", "true"):
        label += " · forced"
    return {"codec": c, "language": lang or None, "kind": kind, "label": label}


def summarize_playback(pb: Dict[str, Any], kind: str) -> Dict[str, Optional[str]]:
    """two short labels for a card badge and a history row: what the file is
    ('4K HEVC', 'FLAC 24-bit · 96 kHz', 'AAC 64 kbps') and the main thing that
    changed on the way ('1080p → 720p', 'Subtitles burned in', 'DTS-HD MA → AC3')."""
    pb = pb or {}
    v = pb.get("video") or {}
    a = pb.get("audio") or {}
    vs, vo = v.get("source") or {}, v.get("stream") or {}
    as_, ao = a.get("source") or {}, a.get("stream") or {}
    if vs:
        quality = " ".join(x for x in (vs.get("resolution"), vs.get("codec")) if x) or None
    elif as_:
        quality = as_.get("quality") or as_.get("codec") or None
    else:
        quality = None
    change = None
    subs = pb.get("subtitles") or {}
    if subs.get("decision") == "burn":
        change = "Subtitles burned in"
    elif v.get("changed") and vs.get("resolution") and vo.get("resolution") and vs["resolution"] != vo["resolution"]:
        change = f"{vs['resolution']} → {vo['resolution']}"
    elif v.get("changed") and vs.get("hdr") and not vo.get("hdr"):
        change = f"{vs['hdr']} → SDR"
    elif v.get("changed") and vs.get("codec") and vo.get("codec") and vs["codec"] != vo["codec"]:
        change = f"{vs['codec']} → {vo['codec']}"
    elif a.get("changed") and as_.get("codec") and ao.get("codec") and as_["codec"] != ao["codec"]:
        change = f"{as_['codec']} → {ao['codec']}"
    elif a.get("changed") and as_.get("channel_layout") and ao.get("channel_layout"):
        change = f"{as_['channel_layout']} → {ao['channel_layout']}"
    elif (pb.get("container") or {}).get("changed"):
        c = pb["container"]
        change = f"{c['source']} → {c['stream']}"
    return {"quality": quality, "change": change}
