"""artwork and lyrics for music apps, from what SoulSync already has.

artwork: the Library v2 artwork cache (core.library2.artwork), album art
for a song, resized to the size the app asks for (rounded up to a few
steps so a cache of sizes stays small) and kept next to the cache.

lyrics: the file's own (sidecar .lrc, then the tags), then LRCLib, the
same order as the web player. synced lyrics become structured lines.
"""

from __future__ import annotations

import io
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("subsonic.media")

SIZE_STEPS = (64, 128, 256, 300, 400, 512, 600, 800, 1000, 1200, 1500)
REMOTE_LYRICS_WAIT = 5.0
_resize_lock = threading.Lock()


def size_step(size: Any) -> Optional[int]:
    """the cached size for a requested one (None: the original)."""
    try:
        n = int(size)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    for s in SIZE_STEPS:
        if n <= s:
            return s
    return None


def sized_image(src: str, size: Optional[int], cache_dir: str) -> str:
    """``src`` resized so its longer side is ``size`` (never upscaled),
    cached; ``src`` itself when no size, no Pillow, or anything fails."""
    if not size:
        return src
    try:
        base = os.path.splitext(os.path.basename(src))[0]
        out = os.path.join(cache_dir, "%s_%d.jpg" % (base, size))
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            return out
        from PIL import Image
        with _resize_lock:
            os.makedirs(cache_dir, exist_ok=True)
            with Image.open(src) as img:
                if max(img.size) <= size:
                    return src
                img = img.convert("RGB")
                img.thumbnail((size, size))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=88)
            tmp = out + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(buf.getvalue())
            os.replace(tmp, out)
        return out
    except Exception:  # noqa: BLE001 - the original is still a picture
        logger.debug("resize of %s failed", src, exc_info=True)
        return src


def artwork_path(database, conn, config_manager, kind: str, entity_id: int) -> Optional[str]:
    """the cached jpg for an 'album' or 'artist', built when missing."""
    from core.library2 import artwork as A
    try:
        cached = A.artwork_file(database, kind, entity_id)
        if cached.exists():
            return str(cached)
        return A.build_artwork(database, conn, config_manager, kind, int(entity_id))
    except Exception:  # noqa: BLE001 - no art is a 404, not a 500
        logger.debug("artwork for %s %s failed", kind, entity_id, exc_info=True)
        return None


def sized_cache_dir(database) -> str:
    from core.library2.artwork import artwork_dir
    return str(artwork_dir(database) / "sized")


# ── lyrics ───────────────────────────────────────────────────────────────

_LRC_LINE = re.compile(r"^((?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+)(.*)$")
_LRC_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_TAG = re.compile(r"^\[[a-zA-Z]+:.*\]$")


def parse_lrc(text: str) -> List[Dict[str, Any]]:
    """synced lrc to [{start (ms), value}] in time order. lines with
    several stamps repeat; tag lines ([ar:], [offset:]) are dropped."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or (_LRC_TAG.match(line) and not _LRC_LINE.match(line)):
            continue
        m = _LRC_LINE.match(line)
        if not m:
            continue
        words = m.group(2).strip()
        for mm, ss, frac in _LRC_STAMP.findall(m.group(1)):
            ms = int(frac.ljust(3, "0")[:3]) if frac else 0
            out.append({"start": (int(mm) * 60 + int(ss)) * 1000 + ms, "value": words})
    out.sort(key=lambda x: x["start"])
    return out


def plain_lines(text: str) -> List[Dict[str, Any]]:
    return [{"value": ln.rstrip()} for ln in (text or "").splitlines()]


def strip_lrc(text: str) -> str:
    return "\n".join(x["value"] for x in parse_lrc(text))


def lyrics_for(local_path: Optional[str], *, title: str, artist: str, album: str = "",
               duration_s: int = 0, remote: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """(synced lrc, plain) from the file, then LRCLib."""
    synced = plain = None
    if local_path:
        try:
            from core.lyrics_local import read_local
            synced, plain, _src = read_local(local_path)
        except Exception:  # noqa: BLE001
            logger.debug("local lyrics read failed", exc_info=True)
    if (synced or plain) or not remote or not title or not artist:
        return synced, plain
    try:
        from core.lyrics_client import lyrics_client
        got = lyrics_client.lookup_remote_lyrics(title, artist, album or None, duration_s or None,
                                                 max_wait_seconds=REMOTE_LYRICS_WAIT)
        data = getattr(got, "data", None) if getattr(got, "status", "") == "found" else None
        if data is not None:
            synced = getattr(data, "synced_lyrics", None) or None
            plain = getattr(data, "plain_lyrics", None) or None
    except Exception:  # noqa: BLE001 - no lyrics is an empty list
        logger.debug("remote lyrics failed", exc_info=True)
    return synced, plain


def structured(synced: Optional[str], plain: Optional[str], *, artist: str, title: str) -> List[Dict[str, Any]]:
    """the OpenSubsonic structuredLyrics list: synced first when there is
    one, then plain."""
    out = []
    lines = parse_lrc(synced) if synced else []
    if lines:
        out.append({"displayArtist": artist, "displayTitle": title, "lang": "xxx", "offset": 0,
                    "synced": True, "line": lines})
    text = plain or (strip_lrc(synced) if synced else "")
    if text.strip():
        out.append({"displayArtist": artist, "displayTitle": title, "lang": "xxx", "offset": 0,
                    "synced": False, "line": plain_lines(text)})
    return out


__all__ = ["SIZE_STEPS", "size_step", "sized_image", "artwork_path", "sized_cache_dir", "parse_lrc",
           "plain_lines", "strip_lrc", "lyrics_for", "structured"]
