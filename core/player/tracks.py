"""a video's audio and subtitle tracks, and its subtitles as WebVTT.

where tracks come from:
  * the file's own streams, read with ffprobe (the header only, so quick
    even over the network). kept per file: path, size and mtime, or the
    media file id for a stream relayed from a media server
  * subtitle files beside the video (``<video stem>.<lang>.srt`` and the
    like): what soulsync's subtitle downloads and most tools write

a subtitle is text (srt, ass, webvtt, mov_text: the player draws it) or
an image (pgs, vobsub: burned into the picture by the converter).

WebVTT for a text subtitle is made once by ffmpeg and cached on disk: an
embedded one means reading the whole file, which can take a while on a
big file or a sleeping drive. later asks are instant.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.tracks")

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer"}
IMAGE_CODECS = {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "dvb_subtitle", "xsub"}
SIDECAR_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}

_probe_cache: Dict[Any, Dict[str, Any]] = {}
_probe_lock = threading.Lock()


def file_key(path: Optional[str], fallback: Any) -> Any:
    """what a probe is kept under: the file as it is now, or the caller's id"""
    if path:
        try:
            st = os.stat(path)
            return ("file", path, st.st_size, int(st.st_mtime))
        except OSError:
            pass
    return ("id", fallback)


def parse_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    """ffprobe's json (streams) as the player's tracks, plus the picture
    streams and the container the info sheet shows. pure."""
    audio: List[Dict[str, Any]] = []
    subs: List[Dict[str, Any]] = []
    video: List[Dict[str, Any]] = []
    for st in data.get("streams") or []:
        kind = st.get("codec_type")
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        disp = st.get("disposition") or {}
        codec = str(st.get("codec_name") or "").lower()
        lang = str(tags.get("language") or "").lower() or None
        if lang in ("und", "unk"):
            lang = None
        common = {"language": lang, "title": tags.get("title") or None, "codec": codec,
                  "default": bool(disp.get("default")), "forced": bool(disp.get("forced"))}
        if kind == "audio":
            audio.append({"index": len(audio), **common, "channels": st.get("channels"),
                          "layout": st.get("channel_layout") or None,
                          "plays": codec in BROWSER_AUDIO,
                          "bitrate": _num(st.get("bit_rate")), "sample_rate": _num(st.get("sample_rate"))})
        elif kind == "subtitle":
            n = sum(1 for x in subs if not x.get("external"))
            subs.append({"id": f"s{n}", **common, "external": False,
                         "sdh": bool(disp.get("hearing_impaired")),
                         "kind": "image" if codec in IMAGE_CODECS else
                                 "text" if codec in TEXT_CODECS else "unknown"})
        elif kind == "video" and not disp.get("attached_pic"):
            video.append({"codec": codec, "profile": st.get("profile") or None,
                          "width": _num(st.get("width")), "height": _num(st.get("height")),
                          "fps": _fps(st.get("avg_frame_rate") or st.get("r_frame_rate")),
                          "bit_depth": _bit_depth(st), "transfer": st.get("color_transfer") or None,
                          "bitrate": _num(st.get("bit_rate"))})
    fmt = data.get("format") or {}
    return {"audio": audio, "subtitles": subs, "video": video,
            "format": {"name": fmt.get("format_name") or None, "bitrate": _num(fmt.get("bit_rate")),
                       "duration": _num(fmt.get("duration"), float)}}


def _num(value: Any, kind: Any = int) -> Any:
    try:
        n = kind(float(value)) if kind is int else kind(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _fps(rate: Any) -> Optional[float]:
    """"24000/1001" -> 23.976"""
    try:
        top, _, bottom = str(rate or "").partition("/")
        value = float(top) / float(bottom or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value > 0 else None


def _bit_depth(st: Dict[str, Any]) -> Optional[int]:
    """from the stream's own count, else its pixel format (yuv420p10le: 10)"""
    raw = _num(st.get("bits_per_raw_sample"))
    if raw:
        return raw
    m = re.search(r"p(\d{2})(?:le|be)$", str(st.get("pix_fmt") or ""))
    if m:
        return int(m.group(1))
    return 8 if st.get("pix_fmt") else None


_LANG_IN_NAME = re.compile(r"\.([a-z]{2,3}(?:-[a-z]{2,4})?)(?:\.(forced|sdh|cc|hi))*$", re.I)


def sidecars(video_path: str) -> List[Dict[str, Any]]:
    """subtitle files beside the video, named after it. the language (and
    forced / sdh) comes from the name: ``Movie.en.forced.srt``"""
    folder, name = os.path.split(video_path)
    stem = os.path.splitext(name)[0]
    try:
        entries = sorted(os.listdir(folder or "."))
    except OSError:
        return []
    out = []
    for entry in entries:
        base, ext = os.path.splitext(entry)
        if ext.lower() not in SIDECAR_EXTS or not base.startswith(stem):
            continue
        rest = base[len(stem):]
        if rest and not rest.startswith("."):
            continue  # another video's name that starts the same way
        m = _LANG_IN_NAME.search(rest) if rest else None
        flags = rest.lower().split(".")
        lang = m.group(1).lower() if m else None
        # the ai marker (``Movie.ai.en.srt``) is never the language
        if lang == "ai":
            lang = None
        out.append({"id": f"x{len(out)}", "language": lang,
                    "title": None, "codec": ext.lower().lstrip("."), "default": False,
                    "forced": "forced" in flags,
                    "sdh": "sdh" in flags or "cc" in flags or ("hi" in flags and m is not None and m.group(1).lower() != "hi"),
                    "ai": "ai" in flags,
                    "external": True, "kind": "text", "path": os.path.join(folder, entry)})
    return out


def sidecar_key(path: str) -> Any:
    """a subtitle file's cache key: its path, size and mtime, so a rewrite
    (an upgrade, a resync, a manual replace) is a new WebVTT at once"""
    try:
        st = os.stat(path)
        return ("sidecar", path, st.st_size, getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    except OSError:
        return ("sidecar", path, None, None)


def sidecar_sig(path: str) -> str:
    """size and mtime as one string (the saved delay's check)"""
    key = sidecar_key(path)
    return "" if key[2] is None else f"{key[2]}:{key[3]}"


def parse_chapters(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ffprobe's chapters as ``[{start, end, title}]`` in seconds, in order.
    pure. a file with one chapter (or none that say when they start) has no
    chapters worth showing; a chapter with no name is "Chapter N"."""
    out: List[Dict[str, Any]] = []
    for ch in data.get("chapters") or []:
        try:
            start = float(ch.get("start_time"))
            end = float(ch.get("end_time")) if ch.get("end_time") is not None else None
        except (TypeError, ValueError):
            continue
        if start < 0 or (end is not None and end <= start):
            continue
        tags = {k.lower(): v for k, v in (ch.get("tags") or {}).items()}
        out.append({"start": round(start, 3), "end": round(end, 3) if end is not None else None,
                    "title": str(tags.get("title") or "").strip()})
    out.sort(key=lambda c: c["start"])
    if len(out) < 2:
        return []
    for i, c in enumerate(out):
        c["title"] = c["title"] or f"Chapter {i + 1}"
    return out[:200]


def probe(ffprobe: str, source: str, key: Any) -> Dict[str, List[Dict[str, Any]]]:
    """the source's own tracks, kept per ``key``. can't read it: none."""
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    cmd = [ffprobe, "-v", "error", "-show_entries",
           "stream=index,codec_type,codec_name,profile,channels,channel_layout,bit_rate,sample_rate,"
           "width,height,avg_frame_rate,r_frame_rate,pix_fmt,bits_per_raw_sample,color_transfer"
           ":stream_tags=language,title:stream_disposition=default,forced,hearing_impaired,attached_pic"
           ":format=format_name,bit_rate,duration"
           ":chapter=start_time,end_time:chapter_tags=title", "-of", "json", source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60).stdout  # noqa: S603 - our own argv
        data = json.loads(out or "{}")
        tracks = parse_probe(data)
        tracks["chapters"] = parse_chapters(data)
    except Exception:  # noqa: BLE001 - no tracks is still a playable video
        logger.warning("couldn't read the tracks of %s", source if "://" not in source else "a relayed stream",
                       exc_info=True)
        return {"audio": [], "subtitles": []}
    with _probe_lock:
        if len(_probe_cache) > 2000:
            _probe_cache.clear()
        _probe_cache[key] = tracks
    return tracks


def cached(key: Any) -> Optional[Dict[str, Any]]:
    """a probe already made for ``key``, never a new one"""
    with _probe_lock:
        return _probe_cache.get(key)


def cached_like(file_name: str, size: Any) -> Optional[Dict[str, Any]]:
    """a probe made for a file of this name and size, wherever it was read
    from (the info sheet knows the file, not the path the player used)"""
    if not file_name:
        return None
    with _probe_lock:
        for key, value in _probe_cache.items():
            if (isinstance(key, tuple) and len(key) == 4 and key[0] == "file"
                    and os.path.basename(str(key[1]).replace("\\", "/")) == file_name
                    and (not size or key[2] == size)):
                return value
    return None


def all_tracks(ffprobe: str, source: str, key: Any, local_path: Optional[str]) -> Dict[str, Any]:
    """the file's tracks and the subtitle files beside it (only for a file on
    this box: a relayed stream has no folder to look in)"""
    own = probe(ffprobe, source, key)
    subs = list(own["subtitles"]) + (sidecars(local_path) if local_path else [])
    return {"audio": own["audio"], "subtitles": subs, "chapters": own.get("chapters") or []}


def public(tracks: Dict[str, Any]) -> Dict[str, Any]:
    """what a client sees: never a sidecar's path"""
    return {"audio": tracks["audio"],
            "subtitles": [{k: v for k, v in s.items() if k != "path"} for s in tracks["subtitles"]],
            "chapters": tracks.get("chapters") or []}


# ── WebVTT ───────────────────────────────────────────────────────────────

_vtt_locks: Dict[str, threading.Lock] = {}
_vtt_guard = threading.Lock()


def cache_dir() -> Path:
    from database.player_database import default_path
    return Path(default_path()).parent / "player-cache" / "subtitles"


def to_vtt(ffmpeg: str, source: str, stream: Optional[int], key: Any, out_dir: Optional[Path] = None) -> str:
    """a text subtitle as WebVTT: an embedded stream (``stream``: its number
    among the file's subtitles) or a subtitle file (``stream`` None, source
    is the file). made once, then read from the cache."""
    folder = out_dir or cache_dir()
    name = hashlib.sha1(repr((key, stream)).encode()).hexdigest() + ".vtt"
    target = folder / name
    with _vtt_guard:
        lock = _vtt_locks.setdefault(name, threading.Lock())
    with lock:  # two viewers asking at once make it once
        if target.exists():
            return target.read_text(encoding="utf-8", errors="replace")
        folder.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", source]
        cmd += ["-map", f"0:s:{stream}"] if stream is not None else []
        cmd += ["-f", "webvtt", "pipe:1"]
        res = subprocess.run(cmd, capture_output=True, timeout=600)  # noqa: S603 - our own argv
        text = res.stdout.decode("utf-8", errors="replace")
        if res.returncode != 0 or not text.startswith("WEBVTT"):
            raise RuntimeError((res.stderr or b"").decode("utf-8", "replace")[-300:] or "no subtitles")
        tmp = target.with_suffix(".part")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        return text
