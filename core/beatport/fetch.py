"""fetch a beatport list by its ref and read it from the page json.

one request per page, through the scraper's session so the cloudflare /
flaresolverr fallback still applies. pages are kept for a few minutes because
one page holds several lists (the homepage has both top 10s), and opening a
list right after browsing it shouldn't pay for a second cloudflare solve.

every track fetched here is also filed in the metadata cache under its url
and its beatport id. the url entry is what /api/beatport/enrich-tracks reads,
so a good fetch overwrites anything the old enrichment cached wrong; the id
entry is how a mirrored beatport track finds its isrc later.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Union

from core.beatport.next_data import (
    chart_meta,
    extract_next_data,
    list_tracks,
    release_meta,
    track_details,
)
from core.beatport.refs import BeatportRef, parse_ref
from utils.logging_config import get_logger

logger = get_logger("beatport.fetch")

PAGE_TTL = 600  # seconds
_page_lock = threading.Lock()
_pages: Dict[str, tuple] = {}  # url -> (fetched_at, next_data)

# metadata cache entity id for the by-id entry
CACHE_ID_PREFIX = 'id:'


def _scraper():
    from beatport_unified_scraper import BeatportUnifiedScraper
    return BeatportUnifiedScraper()


def clear_page_cache() -> None:
    with _page_lock:
        _pages.clear()


def fetch_next_data(url: str, scraper: Any = None, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """the page's next.js json, or None. never raises."""
    now = time.time()
    if use_cache:
        with _page_lock:
            hit = _pages.get(url)
        if hit and now - hit[0] < PAGE_TTL:
            return hit[1]
    try:
        response = (scraper or _scraper())._get(url, timeout=20)
    except Exception as e:  # noqa: BLE001 - a dead fetch is a None, not a crash
        logger.warning("beatport fetch failed for %s: %s", url, e)
        return None
    status = getattr(response, 'status_code', 0) or 0
    if status >= 400:
        # loud on purpose: a 403 here is cloudflare, and the fix is flaresolverr
        logger.warning("beatport returned %s for %s (cloudflare? set up flaresolverr)", status, url)
        return None
    data = extract_next_data(getattr(response, 'text', None) or getattr(response, 'content', b''))
    if data is None:
        logger.warning("no __NEXT_DATA__ on %s, beatport may have changed the page", url)
        return None
    with _page_lock:
        if len(_pages) > 64:
            _pages.clear()
        _pages[url] = (now, data)
    return data


def remember_tracks(tracks: List[Dict[str, Any]]) -> None:
    """file each track in the metadata cache by url and by beatport id."""
    try:
        from core.metadata.cache import get_metadata_cache
        cache = get_metadata_cache()
    except Exception as e:  # noqa: BLE001
        logger.debug("metadata cache unavailable: %s", e)
        return
    for track in tracks or []:
        try:
            if track.get('url'):
                cache.store_entity('beatport', 'track', track['url'], track)
            if track.get('beatport_id'):
                cache.store_entity('beatport', 'track', f"{CACHE_ID_PREFIX}{track['beatport_id']}", track)
        except Exception as e:  # noqa: BLE001
            logger.debug("beatport track cache write failed: %s", e)


def cached_track(beatport_id: Any) -> Optional[Dict[str, Any]]:
    """a track remembered by remember_tracks, by its beatport id."""
    text = str(beatport_id or '').strip()
    if text.startswith('beatport_'):
        text = text[len('beatport_'):]
    if not text.isdigit():
        return None
    try:
        from core.metadata.cache import get_metadata_cache
        return get_metadata_cache().get_entity('beatport', 'track', f"{CACHE_ID_PREFIX}{text}")
    except Exception as e:  # noqa: BLE001
        logger.debug("beatport track cache read failed: %s", e)
        return None


def fetch_list(ref: Union[str, BeatportRef], scraper: Any = None, limit: Optional[int] = None,
               genre_name: str = '', use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """{'ref', 'name', 'image', 'url', 'creator', 'tracks'} for one list, or
    None when the ref is junk or beatport didn't answer."""
    parsed = ref if isinstance(ref, BeatportRef) else parse_ref(ref)
    if parsed is None:
        return None
    data = fetch_next_data(parsed.url, scraper, use_cache=use_cache)
    if data is None:
        return None

    name = parsed.default_name(genre_name)
    image = ''
    creator = 'Beatport'
    if parsed.kind == 'chart':
        meta = chart_meta(data) or {}
        name = meta.get('name') or name
        image = meta.get('image') or ''
        creator = meta.get('creator') or creator
    elif parsed.kind == 'release':
        meta = release_meta(data) or {}
        name = meta.get('name') or name
        image = meta.get('image') or ''
        creator = ', '.join(a['name'] for a in meta.get('artists') or []) or creator

    tracks = list_tracks(data, parsed.list_kind, list_name=name, limit=limit)
    if tracks is None:
        logger.warning("beatport page %s has no %s list", parsed.url, parsed.list_kind)
        return None
    if not image and tracks:
        image = tracks[0].get('release_image') or ''
    remember_tracks(tracks)
    return {
        'ref': str(parsed),
        'name': name,
        'image': image,
        'url': parsed.url,
        'creator': creator,
        'refreshable': parsed.refreshable,
        'tracks': tracks,
    }


def fetch_track(url: str, scraper: Any = None) -> Optional[Dict[str, Any]]:
    """one track page, normalised. the url is kept as given (the page json
    has no slug for the track itself)."""
    data = fetch_next_data(url, scraper)
    if data is None:
        return None
    track = track_details(data)
    if track is None:
        logger.warning("no track details on %s", url)
        return None
    track['url'] = url
    remember_tracks([track])
    return track
