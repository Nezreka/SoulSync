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


def _thumb_by_id(thumbs, keyword):
    """The highest-res thumbnail whose yt-dlp id mentions ``keyword`` (e.g.
    'avatar' / 'banner'), or None — channels expose both in one thumbnails list."""
    hits = [t for t in (thumbs or []) if isinstance(t, dict) and t.get("url")
            and keyword in str(t.get("id", "")).lower()]
    return _best_thumb(hits)


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


def _shape_entries(entries, limit):
    """Flat playlist/channel entries → our lightweight video shape (id-less and
    null entries dropped)."""
    out = []
    for e in [x for x in (entries or []) if isinstance(x, dict)][:limit]:
        vid = e.get("id")
        if not vid:
            continue
        dur = e.get("duration")
        out.append({
            "youtube_id": vid,
            "title": e.get("title") or "",
            "published_at": _entry_date(e),
            "duration_seconds": int(dur) if isinstance(dur, (int, float)) else None,
            "thumbnail_url": _best_thumb(e.get("thumbnails")) or e.get("thumbnail"),
            "view_count": e.get("view_count"),
            "description": e.get("description"),
        })
    return out


def shape_channel(info, limit=30):
    """Map a yt-dlp channel info dict to our followable-channel shape."""
    info = info or {}
    videos = _shape_entries(info.get("entries"), limit)
    handle = info.get("uploader_id") or info.get("channel_id_handle")
    if handle and not str(handle).startswith("@"):
        handle = None  # uploader_id is sometimes the UC id, not a handle
    thumbs = info.get("thumbnails")
    return {
        "youtube_id": info.get("channel_id") or info.get("id"),
        "title": info.get("channel") or info.get("uploader") or info.get("title") or "",
        "handle": handle,
        "avatar_url": _thumb_by_id(thumbs, "avatar") or _best_thumb(thumbs),
        "banner_url": _thumb_by_id(thumbs, "banner"),
        "description": info.get("description"),
        "subscriber_count": info.get("channel_follower_count"),
        "view_count": info.get("view_count"),
        "tags": (info.get("tags") or [])[:12],
        "video_count": info.get("playlist_count") or len(videos),
        "videos": videos,
    }


def _cookie_opts():
    """The music client's cookie convention so age/region-gated content works."""
    try:
        from config.settings import config_manager
        cb = config_manager.get("youtube.cookies_browser", "")
        if cb:
            return {"cookiesfrombrowser": (cb,)}
    except Exception:
        pass
    return {}


def _ydl_opts(limit, db=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,        # fast: enumerate uploads without per-video format probing
        "skip_download": True,
        "playlistend": int(limit),
        "user_agent": _UA,
    }
    opts.update(_cookie_opts())
    return opts


def shape_video(info):
    """Map yt-dlp's FULL single-video info dict to our rich video shape (the data
    flat-listing can't give: description, views, likes, duration, tags)."""
    info = info or {}
    dur = info.get("duration")
    return {
        "youtube_id": info.get("id"),
        "title": info.get("title") or "",
        "description": info.get("description"),
        "duration_seconds": int(dur) if isinstance(dur, (int, float)) else None,
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "published_at": _entry_date(info),
        "thumbnail_url": _best_thumb(info.get("thumbnails")) or info.get("thumbnail"),
        "channel_title": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "webpage_url": info.get("webpage_url") or ("https://www.youtube.com/watch?v=" + (info.get("id") or "")),
        "tags": info.get("tags") or [],
    }


def video_detail(video_id, ydl_factory=None):
    """Full metadata for ONE video (non-flat extract) — done lazily on click, the
    way the TV nebula lazy-loads guest stars. Accepts a raw id or a watch URL."""
    if not video_id:
        return None
    vid = str(video_id).strip()
    url = vid if vid.startswith("http") else "https://www.youtube.com/watch?v=" + vid
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "user_agent": _UA}
    opts.update(_cookie_opts())
    info = _extract(url, opts, ydl_factory)
    if not info or not info.get("id"):
        return None
    return shape_video(info)


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


def parse_rss_dates(xml_text):
    """{video_id: 'YYYY-MM-DD'} from a YouTube channel RSS feed (pure, testable)."""
    import xml.etree.ElementTree as ET
    out = {}
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    for entry in root.findall("a:entry", ns):
        vid = entry.find("yt:videoId", ns)
        pub = entry.find("a:published", ns)
        if vid is not None and vid.text and pub is not None and pub.text:
            out[vid.text.strip()] = pub.text.strip()[:10]   # ISO datetime → YYYY-MM-DD
    return out


def channel_recent_dates(channel_id, fetch=None):
    """Real upload dates for a channel's ~15 most-recent videos via its public RSS
    feed — one cheap GET, no yt-dlp, no bot risk. {video_id: 'YYYY-MM-DD'}."""
    cid = str(channel_id or "").strip()
    if not cid:
        return {}
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
    try:
        if fetch is not None:
            return parse_rss_dates(fetch(url) or "")
        import requests
        r = requests.get(url, timeout=10, headers={"User-Agent": _UA})
        return parse_rss_dates(r.text) if r.status_code == 200 else {}
    except Exception as e:
        logger.info("YouTube RSS dates failed for %s: %s", cid, e)
        return {}


def search_channels(query, limit=6, ydl_factory=None):
    """Search YouTube for CHANNELS (the results page filtered to channels) → a few
    {youtube_id, title, handle, avatar_url, subscriber_count} for the search page.
    Best-effort; entries that aren't channels are skipped."""
    from urllib.parse import quote
    q = (query or "").strip()
    if not q:
        return []
    # sp=EgIQAg%3D%3D is YouTube's "Type: Channel" search filter.
    url = "https://www.youtube.com/results?search_query=" + quote(q) + "&sp=EgIQAg%3D%3D"
    info = _extract(url, _ydl_opts(limit * 3), ydl_factory)   # over-fetch; some entries may be videos
    out = []
    for e in [x for x in ((info or {}).get("entries") or []) if isinstance(x, dict)]:
        cid = e.get("channel_id")
        if not cid and str(e.get("id", "")).startswith("UC"):
            cid = e.get("id")
        if not cid:
            m = re.search(r"/channel/(UC[\w-]+)", e.get("url") or "")
            if m:
                cid = m.group(1)
        if not cid or not str(cid).startswith("UC"):
            continue
        title = e.get("channel") or e.get("title") or e.get("uploader") or ""
        if not title:
            continue
        uid = e.get("uploader_id")
        out.append({
            "youtube_id": cid,
            "title": title,
            "handle": uid if str(uid or "").startswith("@") else None,
            "avatar_url": _best_thumb(e.get("thumbnails")) or e.get("thumbnail"),
            "subscriber_count": e.get("channel_follower_count"),
            "video_count": e.get("playlist_count"),
        })
        if len(out) >= limit:
            break
    return out


def channel_playlists(channel_id, limit=50, ydl_factory=None):
    """The channel's playlists (flat) → [{playlist_id, title, video_count,
    thumbnail_url}] — rendered as "seasons" on the channel page. Lazy-loaded."""
    if not channel_id:
        return []
    url = "https://www.youtube.com/channel/" + str(channel_id) + "/playlists"
    info = _extract(url, _ydl_opts(limit), ydl_factory)
    out = []
    for e in [x for x in ((info or {}).get("entries") or []) if isinstance(x, dict)][:limit]:
        pid = e.get("id")
        if not pid:
            continue
        out.append({
            "playlist_id": pid,
            "title": e.get("title") or "",
            "video_count": e.get("playlist_count") or e.get("video_count"),
            "thumbnail_url": _best_thumb(e.get("thumbnails")) or e.get("thumbnail"),
        })
    return out


def playlist_videos(playlist_id, limit=50, ydl_factory=None):
    """A playlist's videos (flat), in the same shape as channel uploads."""
    if not playlist_id:
        return []
    url = "https://www.youtube.com/playlist?list=" + str(playlist_id)
    info = _extract(url, _ydl_opts(limit), ydl_factory)
    return _shape_entries((info or {}).get("entries"), limit)
