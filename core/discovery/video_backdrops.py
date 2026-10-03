"""a music video to play, muted, behind a discover banner.

spotify has canvas loops; nobody else can get those. what soulsync can get is
the artist's actual music video, through the same yt-dlp search the artist
page's music videos use. one search per artist (or release), then the pick is
remembered for a month, and a miss for a week, so a page load never searches.

the pick is strict on purpose. a lyric video, a live phone recording, a
reaction or an hour-long mix behind the hero looks worse than the photo it
replaced, so anything that isn't clearly the artist's official video is no
video at all.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("discovery.video_backdrops")

HIT_TTL = 30 * 86400
MISS_TTL = 7 * 86400
MIN_SECONDS = 90
MAX_SECONDS = 600
SEARCH_RESULTS = 8

# titles that are never a good backdrop
_REJECT = re.compile(
    r"\b(lyric|lyrics|karaoke|reaction|reacts|cover|tutorial|lesson|full album|"
    r"playlist|mix\b|hour|1 hour|slowed|reverb|sped up|8d|nightcore|visualizer|audio only|"
    r"remix contest|behind the scenes|interview)\b",
    re.IGNORECASE,
)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def pick_video(results: Sequence[Any], artist: str, title: Optional[str] = None) -> Optional[str]:
    """the best official video id for this artist (and release), or None.

    each result needs video_id, title, channel, duration (s), view_count."""
    want_artist = _norm(artist)
    want_title = _norm(title) if title else ''
    if not want_artist:
        return None
    best, best_score = None, 0.0
    for r in results:
        get = r.get if isinstance(r, dict) else (lambda k, d=None, _r=r: getattr(_r, k, d))
        vid = get('video_id')
        vt = str(get('title') or '')
        channel = _norm(get('channel'))
        duration = int(get('duration') or 0)
        if not vid or _REJECT.search(vt):
            continue
        if not (MIN_SECONDS <= duration <= MAX_SECONDS):
            continue
        text = _norm(vt)
        # it has to be this artist: in the title, or their own channel
        if want_artist not in text and want_artist not in channel:
            continue
        score = 1.0
        if 'official' in text and 'video' in text:
            score += 3
        elif 'official' in text or 'music video' in text:
            score += 1.5
        if want_artist in channel or channel.endswith('vevo') or 'vevo' in channel:
            score += 2
        if want_title:
            if want_title in text:
                score += 4
            else:
                # asked for a release and this isn't it: only a weak fallback
                score -= 2
        if re.search(r"\blive\b", vt, re.IGNORECASE):
            score -= 1.5
        score += math.log10(max(int(get('view_count') or 0), 1)) / 4
        if score > best_score:
            best, best_score = vid, score
    return best if best_score >= 2.0 else None


def _key(artist: str, title: Optional[str]) -> str:
    return f"bg_video:{_norm(artist)}|{_norm(title) if title else ''}"


def cached_pick(database, artist: str, title: Optional[str], now: Optional[float] = None):
    """(found, video_id). found is False when there's no fresh answer yet."""
    now = now or time.time()
    try:
        raw = database.get_metadata(_key(artist, title))
        if not raw:
            return False, None
        entry = json.loads(raw)
        ttl = HIT_TTL if entry.get('video_id') else MISS_TTL
        if now - float(entry.get('at', 0)) < ttl:
            return True, entry.get('video_id')
    except Exception as e:  # noqa: BLE001 - a bad cache row just searches again
        logger.debug("backdrop cache read failed: %s", e)
    return False, None


def remember(database, artist: str, title: Optional[str], video_id: Optional[str],
             now: Optional[float] = None) -> None:
    try:
        database.set_metadata(_key(artist, title),
                              json.dumps({'video_id': video_id, 'at': now or time.time()}))
    except Exception as e:  # noqa: BLE001
        logger.debug("backdrop cache write failed: %s", e)


def find_backdrop(database, youtube_client, artist: str, title: Optional[str] = None) -> Optional[str]:
    """cached answer if fresh, else one search, remembered either way."""
    found, vid = cached_pick(database, artist, title)
    if found:
        return vid
    if youtube_client is None or not hasattr(youtube_client, 'search_videos'):
        return None
    query = f"{artist} {title} official music video" if title else f"{artist} official music video"
    try:
        import asyncio
        # its own short-lived loop in this request thread: never the shared
        # run_async loop, which a slow yt-dlp call would starve
        results = asyncio.run(asyncio.wait_for(
            youtube_client.search_videos(query, max_results=SEARCH_RESULTS), timeout=20))
    except Exception as e:  # noqa: BLE001 - no video is a fine answer
        logger.debug("backdrop search failed for %r: %s", query, e)
        return None   # a failure isn't a miss: don't remember it
    vid = pick_video(results or [], artist, title)
    remember(database, artist, title, vid)
    return vid
