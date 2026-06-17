"""Resolve a YouTube *channel* reference into a followable channel + its recent
uploads — the data source behind "follow a YouTube channel" on the video side.

yt-dlp only (no API key). The upload list is fetched with ``extract_flat`` so it
stays cheap even for channels with thousands of videos; the trade-off is that
per-video publish dates are sparse in flat mode, so they get backfilled later
(same pattern as the wishlist art backfill). Real *downloads* will additionally
need Deno + ffmpeg; flat *listing* does not.

The URL parsing and the yt-dlp-dict → our-shape mapping are pure functions so
they're unit-testable without touching the network; the one network call goes
through ``_extract`` which accepts an injectable factory for tests.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import yt_dlp
except Exception:  # pragma: no cover - yt-dlp is a hard dep, but stay import-safe
    yt_dlp = None

logger = logging.getLogger(__name__)

# Path prefixes that identify a channel (everything after is the channel key).
# Tabs like /videos, /streams, /shorts, /featured, /playlists are stripped.
_CHANNEL_TABS = {"videos", "streams", "shorts", "featured", "playlists", "community", "about"}
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{2,}$")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def parse_channel_url(raw):
    """Normalize a pasted channel reference to a canonical uploads URL, or return
    None if it isn't a channel (a /watch video, a playlist, the YT home, etc.).

    Accepts: ``https://www.youtube.com/@PlayStation`` (+ /videos etc.),
    ``/channel/UC...``, ``/c/Name``, ``/user/Name``, a bare ``@handle``, or a
    bare ``handle``. Returns ``https://www.youtube.com/<base>/videos``.
    """
    if not raw or not str(raw).strip():
        return None
    raw = str(raw).strip()

    # Bare handle (no scheme, no slash) → treat as @handle.
    if "/" not in raw and "." not in raw and " " not in raw:
        if _HANDLE_RE.match(raw):
            handle = raw if raw.startswith("@") else "@" + raw
            return "https://www.youtube.com/" + handle + "/videos"
        return None

    # Give bare "youtube.com/..." a scheme so urlparse populates netloc.
    parse_target = raw if "://" in raw else "https://" + raw
    try:
        u = urlparse(parse_target)
    except Exception:
        return None
    host = (u.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        return None

    segs = [s for s in (u.path or "").split("/") if s]
    if not segs:
        return None

    first = segs[0]
    if first.startswith("@"):
        base = first                                   # /@handle
    elif first in ("channel", "c", "user") and len(segs) >= 2:
        base = first + "/" + segs[1]                   # /channel/UC.., /c/Name, /user/Name
    else:
        return None                                    # /watch, /playlist, /shorts/<id>, home…

    return "https://www.youtube.com/" + base + "/videos"


def _best_thumb(thumbs):
    """Pick the highest-resolution thumbnail URL from a yt-dlp thumbnails list."""
    if not thumbs:
        return None
    best, best_area = None, -1
    for t in thumbs:
        if not isinstance(t, dict) or not t.get("url"):
            continue
        area = (t.get("width") or 0) * (t.get("height") or 0)
        # preference value as a tie-breaker when dimensions are absent
        score = area if area else (t.get("preference") or 0)
        if score >= best_area:
            best, best_area = t.get("url"), score
    return best


def _entry_date(e):
    """ISO date (YYYY-MM-DD) for a flat entry, or None — flat mode often omits it."""
    ts = e.get("timestamp")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except Exception:
            pass
    ud = e.get("upload_date")  # 'YYYYMMDD'
    if isinstance(ud, str) and len(ud) == 8 and ud.isdigit():
        return f"{ud[0:4]}-{ud[4:6]}-{ud[6:8]}"
    return None


def shape_channel(info, limit=30):
    """Map a yt-dlp channel info dict to our followable-channel shape."""
    info = info or {}
    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    videos = []
    for e in entries[:limit]:
        vid = e.get("id")
        if not vid:
            continue
        dur = e.get("duration")
        videos.append({
            "youtube_id": vid,
            "title": e.get("title") or "",
            "published_at": _entry_date(e),
            "duration_seconds": int(dur) if isinstance(dur, (int, float)) else None,
            "thumbnail_url": _best_thumb(e.get("thumbnails")) or e.get("thumbnail"),
            "view_count": e.get("view_count"),
            "description": e.get("description"),
        })

    handle = info.get("uploader_id") or info.get("channel_id_handle")
    if handle and not str(handle).startswith("@"):
        handle = None  # uploader_id is sometimes the UC id, not a handle
    return {
        "youtube_id": info.get("channel_id") or info.get("id"),
        "title": info.get("channel") or info.get("uploader") or info.get("title") or "",
        "handle": handle,
        "avatar_url": _best_thumb(info.get("thumbnails")),
        "description": info.get("description"),
        "subscriber_count": info.get("channel_follower_count"),
        "video_count": info.get("playlist_count") or len(videos),
        "videos": videos,
    }


def _ydl_opts(limit, db=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,        # fast: enumerate uploads without per-video format probing
        "skip_download": True,
        "playlistend": int(limit),
        "user_agent": _UA,
    }
    # Reuse the music client's cookie convention so age/region-gated channels work.
    try:
        from config.settings import config_manager
        cb = config_manager.get("youtube.cookies_browser", "")
        if cb:
            opts["cookiesfrombrowser"] = (cb,)
    except Exception:
        pass
    return opts


def _extract(url, opts, ydl_factory=None):
    """Run yt-dlp's extract_info; isolated for test injection. Returns a dict or None."""
    factory = ydl_factory or (yt_dlp.YoutubeDL if yt_dlp else None)
    if factory is None:
        logger.warning("yt-dlp unavailable; cannot resolve YouTube channel")
        return None
    try:
        with factory(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.info("YouTube channel resolve failed for %s: %s", url, e)
        return None


def resolve_channel(raw, limit=30, ydl_factory=None, db=None):
    """Resolve a pasted channel reference to ``{youtube_id, title, handle,
    avatar_url, videos:[...], ...}``, or None if it isn't a resolvable channel."""
    url = parse_channel_url(raw)
    if not url:
        return None
    info = _extract(url, _ydl_opts(limit, db), ydl_factory)
    if not info:
        return None
    shaped = shape_channel(info, limit)
    return shaped if shaped.get("youtube_id") else None
