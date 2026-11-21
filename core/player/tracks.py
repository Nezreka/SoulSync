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


def parse_probe(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """ffprobe's json (streams) as the player's tracks. pure."""
    audio: List[Dict[str, Any]] = []
    subs: List[Dict[str, Any]] = []
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
                          "plays": codec in BROWSER_AUDIO})
        elif kind == "subtitle":
            n = sum(1 for x in subs if not x.get("external"))
            subs.append({"id": f"s{n}", **common, "external": False,
                         "kind": "image" if codec in IMAGE_CODECS else
                                 "text" if codec in TEXT_CODECS else "unknown"})
    return {"audio": audio, "subtitles": subs}


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
        out.append({"id": f"x{len(out)}", "language": (m.group(1).lower() if m else None),
                    "title": None, "codec": ext.lower().lstrip("."), "default": False,
                    "forced": "forced" in flags, "sdh": "sdh" in flags or "hi" in flags or "cc" in flags,
                    "external": True, "kind": "text", "path": os.path.join(folder, entry)})
    return out


def probe(ffprobe: str, source: str, key: Any) -> Dict[str, List[Dict[str, Any]]]:
    """the source's own tracks, kept per ``key``. can't read it: none."""
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    cmd = [ffprobe, "-v", "error", "-show_entries",
           "stream=index,codec_type,codec_name,channels,channel_layout:stream_tags=language,title"
           ":stream_disposition=default,forced", "-of", "json", source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout  # noqa: S603 - our own argv
        tracks = parse_probe(json.loads(out or "{}"))
    except Exception:  # noqa: BLE001 - no tracks is still a playable video
        logger.warning("couldn't read the tracks of %s", source if "://" not in source else "a relayed stream",
                       exc_info=True)
        return {"audio": [], "subtitles": []}
    with _probe_lock:
        if len(_probe_cache) > 2000:
            _probe_cache.clear()
        _probe_cache[key] = tracks
    return tracks


def all_tracks(ffprobe: str, source: str, key: Any, local_path: Optional[str]) -> Dict[str, Any]:
    """the file's tracks and the subtitle files beside it (only for a file on
    this box: a relayed stream has no folder to look in)"""
    own = probe(ffprobe, source, key)
    subs = list(own["subtitles"]) + (sidecars(local_path) if local_path else [])
    return {"audio": own["audio"], "subtitles": subs}


def public(tracks: Dict[str, Any]) -> Dict[str, Any]:
    """what a client sees: never a sidecar's path"""
    return {"audio": tracks["audio"],
            "subtitles": [{k: v for k, v in s.items() if k != "path"} for s in tracks["subtitles"]]}


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
