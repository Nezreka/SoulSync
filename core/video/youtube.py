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
import time
from datetime import datetime, timedelta, timezone
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


# No-key bulk date sources: community Piped/Invidious instances. Flaky (they go
# up/down), so we try several and fall back to per-video yt-dlp if none answer.
_PROXY_INSTANCES = [
    ("piped", "https://pipedapi.kavin.rocks"),
    ("piped", "https://pipedapi.adminforge.de"),
    ("piped", "https://api.piped.private.coffee"),
    ("invidious", "https://invidious.nerdvpn.de"),
    ("invidious", "https://inv.nadeko.net"),
]


def _epoch_to_date(v):
    """epoch seconds OR milliseconds → 'YYYY-MM-DD', or None."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 1e12:            # milliseconds
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _vid_from_url(u):
    m = re.search(r"[?&]v=([\w-]+)", u or "")
    return m.group(1) if m else None


def parse_proxy_dates(obj):
    """{video_id: 'YYYY-MM-DD'} from a Piped (relatedStreams) or Invidious (videos)
    channel JSON response — handles both shapes."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for s in (obj.get("relatedStreams") or []):     # Piped
        if not isinstance(s, dict):
            continue
        vid = _vid_from_url(s.get("url")) or s.get("videoId")
        d = _epoch_to_date(s.get("uploaded"))
        if vid and d:
            out[vid] = d
    for v in (obj.get("videos") or []):              # Invidious
        if not isinstance(v, dict):
            continue
        vid = v.get("videoId")
        d = _epoch_to_date(v.get("published"))
        if vid and d:
            out[vid] = d
    return out


def _proxy_get(url, fetch):
    if fetch is not None:
        return fetch(url)
    import requests
    # Short timeout so a dead instance (most public ones are flaky) fails fast and
    # we fall through to the next / to the yt-dlp fallback instead of hanging.
    r = requests.get(url, timeout=4, headers={"User-Agent": _UA, "Accept": "application/json"})
    return r.json() if r.status_code == 200 else None


def _harvest(kind, base, cid, pages, fetch):
    """Walk one instance's channel pages, accumulating {video_id: date}. Follows
    pagination even when an early page is empty-but-has-a-next-token (some Piped
    instances return an empty first page), stopping when there's no token left."""
    from urllib.parse import quote
    out, token = {}, None
    for i in range(max(1, pages)):
        if kind == "piped":
            url = (base + "/channel/" + cid) if token is None \
                else (base + "/nextpage/channel/" + cid + "?nextpage=" + quote(token, safe=""))
        else:  # invidious
            url = base + "/api/v1/channels/" + cid + "/videos" + \
                (("?continuation=" + quote(token, safe="")) if token else "")
        obj = _proxy_get(url, fetch)
        if not isinstance(obj, dict):
            break
        out.update(parse_proxy_dates(obj))
        token = obj.get("nextpage") if kind == "piped" else obj.get("continuation")
        if not token:
            break
    return out


def proxy_channel_dates(channel_id, pages=6, fetch=None, instances=None):
    """Bulk upload dates for a whole channel via a no-key proxy (Piped/Invidious),
    paginated. Tries instances until one answers. {video_id: 'YYYY-MM-DD'} (empty
    if all are down → caller falls back to per-video yt-dlp)."""
    cid = str(channel_id or "").strip()
    if not cid:
        return {}
    for kind, base in (instances or _PROXY_INSTANCES):
        try:
            dates = _harvest(kind, base, cid, pages, fetch)
            if dates:
                return dates
        except Exception:
            continue
    return {}


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


# ── InnerTube: YouTube's own browse API (no key/Java/proxy) ──────────────────
# Same technique NewPipe/yt-dlp use. We call the channel "Videos" tab and read the
# lockupViewModel items: contentId (video id) + a relative "N units ago" string.
# Relative → approximate date (fine for YEAR grouping). One request per ~30 videos,
# paginated via continuation tokens (light on rate limits). Parsing is pure +
# resilient (recursive search rather than a brittle fixed path).
_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"   # public WEB key (stable for years)
_INNERTUBE_CTX = {"client": {"clientName": "WEB", "clientVersion": "2.20240304.00.00", "hl": "en", "gl": "US"}}
_VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"   # selects a channel's "Videos" tab
_INNERTUBE_PAGES = 8
_INNERTUBE_DELAY = 0.6                     # politeness pause between pages
_REL_UNIT_DAYS = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30.44, "year": 365.25}
_REL_RE = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)


def _json_find_all(obj, key, acc):
    if isinstance(obj, dict):
        if key in obj:
            acc.append(obj[key])
        for v in obj.values():
            _json_find_all(v, key, acc)
    elif isinstance(obj, list):
        for v in obj:
            _json_find_all(v, key, acc)
    return acc


def _json_content_strings(obj, acc):
    if isinstance(obj, dict):
        c = obj.get("content")
        if isinstance(c, str):
            acc.append(c)
        for v in obj.values():
            _json_content_strings(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _json_content_strings(v, acc)
    return acc


def relative_to_date(text, now=None):
    """'2 years ago' / '9 hours ago' → approximate 'YYYY-MM-DD', or None."""
    m = _REL_RE.search(text or "")
    if not m:
        return None
    if now is None:
        now = datetime.now(timezone.utc).date()
    days = int(m.group(1)) * _REL_UNIT_DAYS[m.group(2).lower()]
    return (now - timedelta(days=round(days))).isoformat()


def innertube_parse_videos(obj):
    """[(video_id, relative_text)] for VIDEO lockups in an InnerTube browse response."""
    out, seen = [], set()
    for lk in _json_find_all(obj, "lockupViewModel", []):
        if not isinstance(lk, dict) or lk.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        vid = lk.get("contentId")
        if not vid or vid in seen:
            continue
        rel = next((t for t in _json_content_strings(lk.get("metadata"), []) if _REL_RE.search(t)), None)
        if rel:
            seen.add(vid)
            out.append((vid, rel))
    return out


def innertube_continuation(obj):
    """The pagination continuation token (from a continuationItemRenderer), or None."""
    for cir in _json_find_all(obj, "continuationItemRenderer", []):
        if isinstance(cir, dict):
            tok = (cir.get("continuationEndpoint") or {}).get("continuationCommand", {}).get("token")
            if tok:
                return tok
    return None


def _innertube_post(payload, post):
    if post is not None:
        return post(payload)
    import requests
    r = requests.post("https://www.youtube.com/youtubei/v1/browse", params={"key": _INNERTUBE_KEY},
                      json=payload, timeout=10,
                      headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept-Language": "en-US"})
    return r.json() if (r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json")) else None


def innertube_channel_dates(channel_id, pages=_INNERTUBE_PAGES, now=None, post=None):
    """{video_id: 'YYYY-MM-DD'} for a channel's videos via YouTube's own InnerTube
    browse API — no key/Java/proxy, ~1 request per 30 videos. Dates are APPROXIMATE
    (from relative text), which is fine for year-seasons; the exact yt-dlp path can
    refine specific videos later. Bounded + throttled. {} on any failure (→ caller
    falls back)."""
    cid = str(channel_id or "").strip()
    if not cid.startswith("UC"):
        return {}
    if now is None:
        now = datetime.now(timezone.utc).date()
    out = {}
    payload = {"context": _INNERTUBE_CTX, "browseId": cid, "params": _VIDEOS_PARAMS}
    for _ in range(max(1, pages)):
        try:
            j = _innertube_post(payload, post)
        except Exception:
            break
        if not isinstance(j, dict):
            break
        for vid, rel in innertube_parse_videos(j):
            if vid not in out:
                d = relative_to_date(rel, now)
                if d:
                    out[vid] = d
        token = innertube_continuation(j)
        if not token:
            break
        payload = {"context": _INNERTUBE_CTX, "continuation": token}
        if post is None:
            time.sleep(_INNERTUBE_DELAY)   # rate-limit politeness
    return out


def _lockup_title(lk):
    """The video title from a lockupViewModel (metadata.title.content)."""
    for md in _json_find_all(lk, "lockupMetadataViewModel", []):
        if isinstance(md, dict) and isinstance(md.get("title"), dict):
            c = md["title"].get("content")
            if isinstance(c, str) and c:
                return c
    return None


def _lockup_thumb(lk):
    """The first thumbnail url from a lockupViewModel (contentImage…sources[0].url)."""
    for srcs in _json_find_all(lk.get("contentImage") or {}, "sources", []):
        if isinstance(srcs, list) and srcs and isinstance(srcs[0], dict) and srcs[0].get("url"):
            return srcs[0]["url"]
    return None


_DUR_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")               # 12:34 / 1:02:03 (the overlay badge)
_VIEWS_RE = re.compile(r"([\d.,]+)\s*([KMB]?)\s+views?", re.I)    # "2.6M views", "1,234 views"
_VIEW_MULT = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}


def _json_all_strings(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _json_all_strings(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _json_all_strings(v, acc)
    elif isinstance(obj, str):
        acc.append(obj)
    return acc


def parse_view_count(text):
    """'2.6M views' → 2600000, '1,234 views' → 1234, else None (approximate)."""
    m = _VIEWS_RE.search(text or "")
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")) * _VIEW_MULT.get(m.group(2).upper(), 1))
    except ValueError:
        return None


def _lockup_duration(lk):
    """The duration overlay badge ('12:34') from a lockupViewModel, or None."""
    return next((s for s in _json_all_strings(lk, []) if _DUR_RE.match(s)), None)


def _lockup_views(lk):
    """Approximate view count parsed from the lockup's metadata text, or None."""
    return next((v for v in (parse_view_count(s) for s in _json_content_strings(lk.get("metadata"), []))
                 if v is not None), None)


def innertube_parse_video_items(obj):
    """Full per-video metadata for VIDEO lockups in an InnerTube browse/continuation
    response: [{youtube_id, title, thumbnail_url, duration, view_count, relative}].
    Same source as innertube_parse_videos but keeps the title/thumbnail/duration/
    views so the channel page can list the whole catalog (not just date them)."""
    out, seen = [], set()
    for lk in _json_find_all(obj, "lockupViewModel", []):
        if not isinstance(lk, dict) or lk.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        vid = lk.get("contentId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        rel = next((t for t in _json_content_strings(lk.get("metadata"), []) if _REL_RE.search(t)), None)
        out.append({"youtube_id": vid, "title": _lockup_title(lk), "thumbnail_url": _lockup_thumb(lk),
                    "duration": _lockup_duration(lk), "view_count": _lockup_views(lk), "relative": rel})
    return out


def innertube_channel_videos_page(channel_id, continuation=None, now=None, post=None):
    """ONE InnerTube page of a channel's videos (with metadata) + the next token:
    {"videos": [{youtube_id, title, thumbnail_url, published_at}], "continuation": token|None}.
    Stateless — hand the returned token back to fetch the next page, so the caller
    can stream the FULL catalog in batches (O(n), each page fetched once). {} fields
    / None token on any failure."""
    cid = str(channel_id or "").strip()
    if not continuation and not cid.startswith("UC"):
        return {"videos": [], "continuation": None}
    if now is None:
        now = datetime.now(timezone.utc).date()
    payload = ({"context": _INNERTUBE_CTX, "continuation": continuation} if continuation
               else {"context": _INNERTUBE_CTX, "browseId": cid, "params": _VIDEOS_PARAMS})
    try:
        j = _innertube_post(payload, post)
    except Exception:
        return {"videos": [], "continuation": None}
    if not isinstance(j, dict):
        return {"videos": [], "continuation": None}
    videos = []
    for it in innertube_parse_video_items(j):
        it["published_at"] = relative_to_date(it.pop("relative", None), now)
        videos.append(it)
    return {"videos": videos, "continuation": innertube_continuation(j)}


def innertube_channel_catalog(channel_id, pages=_INNERTUBE_PAGES, now=None, post=None):
    """A channel's video catalog (list + approximate dates) via InnerTube, up to
    ``pages`` pages: [{youtube_id, title, thumbnail_url, published_at}]. Like
    innertube_channel_dates but keeps title/thumbnail so callers can REMEMBER the
    whole list, not just date it. Bounded + throttled; [] on any failure."""
    cid = str(channel_id or "").strip()
    if not cid.startswith("UC"):
        return []
    if now is None:
        now = datetime.now(timezone.utc).date()
    out, seen, token = [], set(), None
    for _ in range(max(1, pages)):
        page = innertube_channel_videos_page(cid, continuation=token, now=now, post=post)
        for v in page.get("videos") or []:
            if v.get("youtube_id") and v["youtube_id"] not in seen:
                seen.add(v["youtube_id"])
                out.append(v)
        token = page.get("continuation")
        if not token:
            break
        if post is None:
            time.sleep(_INNERTUBE_DELAY)   # rate-limit politeness
    return out


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
