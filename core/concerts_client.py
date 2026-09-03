"""Concerts for an artist: upcoming dates and what they actually play live.

Two providers, because they answer two different questions and neither answers
the other's:

  Bandsintown  — "they are playing near you on the 14th". Upcoming tour dates,
                 venue, city, ticket link. Useless for anything historical.
  Setlist.fm   — "here is the set they played in Berlin last month". Song by
                 song, which is the half that connects to a music library:
                 those song names can become a playlist.

Both are treated as OPTIONAL and independent. One being unconfigured or down
must never blank the other, because most people will only ever set up one.

Rate limits are real on both (Setlist.fm asks for a modest cadence, Bandsintown
gates on an app id), so every answer is cached. Concert data does not move fast:
a tour announcement is news over days, not seconds.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("concerts")

SETLISTFM_API = "https://api.setlist.fm/rest/1.0"
BANDSINTOWN_API = "https://rest.bandsintown.com"

# Long on purpose. Tour dates and past setlists are not live data, and both
# providers would rather we asked seldom. A user who just added a key does not
# wait for this: the cache is empty, so the first call goes straight out.
_TTL_SECONDS = 6 * 60 * 60

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _cached(key: str) -> Optional[Any]:
    with _cache_lock:
        hit = _cache.get(key)
    if not hit:
        return None
    stored_at, value = hit
    if time.time() - stored_at > _TTL_SECONDS:
        with _cache_lock:
            _cache.pop(key, None)
        return None
    return value


def _store(key: str, value: Any) -> Any:
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def clear_cache() -> None:
    """Drop everything. Called when the keys change, so a corrected key is not
    shadowed by the failure the old one produced."""
    with _cache_lock:
        _cache.clear()


def _cfg(path: str, default: str = "") -> str:
    try:
        from core.settings import config_manager
        return str(config_manager.get(path, default) or "").strip()
    except Exception:   # noqa: BLE001 - an unreadable config is "not configured"
        return ""


# ── Setlist.fm ───────────────────────────────────────────────────────────────

def setlistfm_configured() -> bool:
    return bool(_cfg("concerts.setlistfm_api_key"))


def _setlist_songs(setlist: Dict[str, Any]) -> List[str]:
    """Flatten a setlist's sets into song titles, in play order.

    Encores are separate sets in the payload and their songs count - a setlist
    without the encore is not the set that was played. Covers keep their own
    title; the 'cover' marker is recorded separately by the caller if wanted.
    """
    out: List[str] = []
    sets = ((setlist.get("sets") or {}).get("set")) or []
    if isinstance(sets, dict):
        sets = [sets]
    for one in sets:
        songs = (one or {}).get("song") or []
        if isinstance(songs, dict):
            songs = [songs]
        for song in songs:
            name = str((song or {}).get("name") or "").strip()
            # A tape (walk-on music) is not something the band played.
            if name and not (song or {}).get("tape"):
                out.append(name)
    return out


def setlistfm_recent(artist_name: str, *, mbid: str = "", limit: int = 5,
                     timeout: int = 12) -> Dict[str, Any]:
    """Recent setlists for an artist, newest first.

    Searches by MBID when we have one. Name matching on setlist.fm is loose
    enough that two bands sharing a name return each other's shows, and an mbid
    is the only thing that actually disambiguates them.
    """
    key = _cfg("concerts.setlistfm_api_key")
    if not key:
        return {"configured": False, "setlists": []}
    ident = (mbid or artist_name or "").strip()
    if not ident:
        return {"configured": True, "setlists": []}

    cache_key = "slfm:%s:%s" % (ident, limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    params: Dict[str, Any] = {"p": 1}
    if mbid:
        params["artistMbid"] = mbid
    else:
        params["artistName"] = artist_name
    try:
        resp = requests.get(
            f"{SETLISTFM_API}/search/setlists",
            params=params,
            headers={"x-api-key": key, "Accept": "application/json",
                     "User-Agent": "SoulSync"},
            timeout=timeout,
        )
        # 404 is setlist.fm's "no setlists for this artist", not a failure.
        if resp.status_code == 404:
            return _store(cache_key, {"configured": True, "setlists": []})
        if resp.status_code == 403:
            return {"configured": True, "setlists": [],
                    "error": "Setlist.fm rejected the API key"}
        if resp.status_code == 429:
            return {"configured": True, "setlists": [],
                    "error": "Setlist.fm is rate limiting, try again shortly"}
        if resp.status_code >= 400:
            return {"configured": True, "setlists": [],
                    "error": "Setlist.fm returned %s" % resp.status_code}
        data = resp.json() or {}
    except Exception as exc:   # noqa: BLE001 - a dead provider is not a page error
        logger.debug("setlist.fm lookup failed for %r", ident, exc_info=True)
        return {"configured": True, "setlists": [], "error": str(exc)}

    raw = data.get("setlist") or []
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for item in raw:
        songs = _setlist_songs(item)
        # A setlist row with no songs is an empty stub - somebody created the
        # show page and never filled it in. Showing it as a concert with no
        # songs reads as a bug in SoulSync.
        if not songs:
            continue
        venue = item.get("venue") or {}
        city = venue.get("city") or {}
        out.append({
            "id": item.get("id"),
            "date": item.get("eventDate"),          # dd-MM-yyyy, per their API
            "venue": venue.get("name") or "",
            "city": city.get("name") or "",
            "country": (city.get("country") or {}).get("name") or "",
            "tour": ((item.get("tour") or {}).get("name")) or "",
            "url": item.get("url") or "",
            "songs": songs,
            "song_count": len(songs),
        })
        if len(out) >= max(1, int(limit)):
            break
    return _store(cache_key, {"configured": True, "setlists": out})


# ── Bandsintown ──────────────────────────────────────────────────────────────

def bandsintown_configured() -> bool:
    return bool(_cfg("concerts.bandsintown_app_id"))


def bandsintown_upcoming(artist_name: str, *, limit: int = 10,
                         timeout: int = 12) -> Dict[str, Any]:
    """Upcoming dates for an artist, soonest first."""
    app_id = _cfg("concerts.bandsintown_app_id")
    if not app_id:
        return {"configured": False, "events": []}
    name = (artist_name or "").strip()
    if not name:
        return {"configured": True, "events": []}

    cache_key = "bit:%s:%s" % (name.lower(), limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    try:
        resp = requests.get(
            # the name goes in the PATH, and a slash or ? in a band name would
            # otherwise change the route rather than the artist
            f"{BANDSINTOWN_API}/artists/{requests.utils.quote(name, safe='')}/events",
            params={"app_id": app_id, "date": "upcoming"},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            return _store(cache_key, {"configured": True, "events": []})
        if resp.status_code >= 400:
            return {"configured": True, "events": [],
                    "error": "Bandsintown returned %s" % resp.status_code}
        data = resp.json()
    except Exception as exc:   # noqa: BLE001
        logger.debug("bandsintown lookup failed for %r", name, exc_info=True)
        return {"configured": True, "events": [], "error": str(exc)}

    # An unknown artist comes back as {} or a warning string rather than [].
    if not isinstance(data, list):
        return _store(cache_key, {"configured": True, "events": []})

    out = []
    for ev in data[: max(1, int(limit))]:
        venue = (ev or {}).get("venue") or {}
        offers = [o for o in ((ev or {}).get("offers") or [])
                  if str(o.get("type") or "").lower() == "tickets"]
        out.append({
            "id": ev.get("id"),
            "datetime": ev.get("datetime"),
            "title": ev.get("title") or "",
            "venue": venue.get("name") or "",
            "city": venue.get("city") or "",
            "region": venue.get("region") or "",
            "country": venue.get("country") or "",
            "url": ev.get("url") or "",
            "tickets_url": (offers[0].get("url") if offers else "") or "",
            "lineup": [x for x in ((ev or {}).get("lineup") or []) if x],
        })
    return _store(cache_key, {"configured": True, "events": out})


# ── One call for the artist page ─────────────────────────────────────────────

def artist_concerts(artist_name: str, *, mbid: str = "", upcoming_limit: int = 10,
                    setlist_limit: int = 5) -> Dict[str, Any]:
    """Both halves, each independent.

    One provider being unconfigured, rate limited or down never blanks the
    other - most installs will only ever have one of the two set up, and a page
    that shows nothing because the half you did not configure is missing would
    look broken rather than partial.
    """
    result: Dict[str, Any] = {
        "artist": artist_name,
        "upcoming": [], "setlists": [],
        "providers": {
            "bandsintown": {"configured": bandsintown_configured()},
            "setlistfm": {"configured": setlistfm_configured()},
        },
    }

    if result["providers"]["bandsintown"]["configured"]:
        bit = bandsintown_upcoming(artist_name, limit=upcoming_limit)
        result["upcoming"] = bit.get("events") or []
        if bit.get("error"):
            result["providers"]["bandsintown"]["error"] = bit["error"]

    if result["providers"]["setlistfm"]["configured"]:
        slf = setlistfm_recent(artist_name, mbid=mbid, limit=setlist_limit)
        result["setlists"] = slf.get("setlists") or []
        if slf.get("error"):
            result["providers"]["setlistfm"]["error"] = slf["error"]

    return result
