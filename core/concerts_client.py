"""Concerts for an artist: upcoming dates and what they actually play live.

Two providers, because they answer two different questions and neither answers
the other's:

  Ticketmaster — "they are playing near you on the 14th". Upcoming dates,
                 venue, city, ticket link. Useless for anything historical.
  Setlist.fm   — "here is the set they played in Berlin last month". Song by
                 song, which is the half that connects to a music library:
                 those song names can become a playlist.

Bandsintown was the obvious pick for the first half and had to be dropped: its
own docs say API access "is available for organizations ... through our
partnership program", and the self-service key is "linked to a single artist" -
for an artist maintaining their own listings, not for looking anybody else up.
Nobody self-hosting this can get a usable key. Ticketmaster's Discovery API
self-registers with a 5000/day free tier, which an individual actually can.

Both are treated as OPTIONAL and independent. One being unconfigured or down
must never blank the other, because most people will only ever set up one.

Rate limits are real on both (Setlist.fm asks for a modest cadence, Ticketmaster
allows 5000 calls a day and 5 a second), so every answer is cached. Concert data
does not move fast: a tour announcement is news over days, not seconds.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("concerts")

SETLISTFM_API = "https://api.setlist.fm/rest/1.0"
TICKETMASTER_API = "https://app.ticketmaster.com/discovery/v2"

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


# ── Ticketmaster ─────────────────────────────────────────────────────────────

def ticketmaster_configured() -> bool:
    return bool(_cfg("concerts.ticketmaster_api_key"))


def _norm(name: Any) -> str:
    """Loose compare key for artist names: case, accents and punctuation off."""
    import re
    import unicodedata
    raw = unicodedata.normalize("NFKD", str(name or ""))
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _event_is_for_artist(event: Dict[str, Any], artist: str) -> bool:
    """Discovery search is keyword-based and generous with it.

    A search for "Air" comes back with tribute acts, festivals that merely list
    the artist, and unrelated events whose blurb happens to contain the word. So
    an event only counts when one of its ATTRACTIONS is the artist - the
    attraction is Ticketmaster's actual artist entity. Falling back to the event
    NAME is deliberately not done: "An Evening of Air Covers" would pass.
    """
    want = _norm(artist)
    if not want:
        return False
    attractions = ((event.get("_embedded") or {}).get("attractions")) or []
    return any(_norm(a.get("name")) == want for a in attractions)


def ticketmaster_upcoming(artist_name: str, *, limit: int = 10,
                          timeout: int = 12) -> Dict[str, Any]:
    """Upcoming dates for an artist, soonest first."""
    key = _cfg("concerts.ticketmaster_api_key")
    if not key:
        return {"configured": False, "events": []}
    name = (artist_name or "").strip()
    if not name:
        return {"configured": True, "events": []}

    cache_key = "tm:%s:%s" % (name.lower(), limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    try:
        resp = requests.get(
            f"{TICKETMASTER_API}/events.json",
            params={
                "apikey": key,
                "keyword": name,
                "classificationName": "music",
                "sort": "date,asc",
                # over-fetch: the attraction filter below discards most of a
                # keyword search, so asking for exactly `limit` would routinely
                # return two or three real dates
                "size": min(100, max(20, int(limit) * 5)),
            },
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"configured": True, "events": [],
                    "error": "Ticketmaster rejected the API key"}
        if resp.status_code == 429:
            return {"configured": True, "events": [],
                    "error": "Ticketmaster is rate limiting, try again shortly"}
        if resp.status_code >= 400:
            return {"configured": True, "events": [],
                    "error": "Ticketmaster returned %s" % resp.status_code}
        data = resp.json() or {}
    except Exception as exc:   # noqa: BLE001
        logger.debug("ticketmaster lookup failed for %r", name, exc_info=True)
        return {"configured": True, "events": [], "error": str(exc)}

    events = ((data.get("_embedded") or {}).get("events")) or []
    out = []
    for ev in events:
        if not _event_is_for_artist(ev, name):
            continue
        venues = ((ev.get("_embedded") or {}).get("venues")) or []
        venue = venues[0] if venues else {}
        start = (ev.get("dates") or {}).get("start") or {}
        out.append({
            "id": ev.get("id"),
            # dateTime is absent for a date with no announced time; localDate
            # still is, and a date with no time beats no row at all.
            "datetime": start.get("dateTime") or start.get("localDate") or "",
            "title": ev.get("name") or "",
            "venue": venue.get("name") or "",
            "city": (venue.get("city") or {}).get("name") or "",
            "region": (venue.get("state") or {}).get("name") or "",
            "country": (venue.get("country") or {}).get("name") or "",
            "url": ev.get("url") or "",
            "tickets_url": ev.get("url") or "",
            "lineup": [a.get("name") for a in
                       (((ev.get("_embedded") or {}).get("attractions")) or [])
                       if a.get("name")],
        })
        if len(out) >= max(1, int(limit)):
            break
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
            "ticketmaster": {"configured": ticketmaster_configured()},
            "setlistfm": {"configured": setlistfm_configured()},
        },
    }

    if result["providers"]["ticketmaster"]["configured"]:
        tm = ticketmaster_upcoming(artist_name, limit=upcoming_limit)
        result["upcoming"] = tm.get("events") or []
        if tm.get("error"):
            result["providers"]["ticketmaster"]["error"] = tm["error"]

    if result["providers"]["setlistfm"]["configured"]:
        slf = setlistfm_recent(artist_name, mbid=mbid, limit=setlist_limit)
        result["setlists"] = slf.get("setlists") or []
        if slf.get("error"):
            result["providers"]["setlistfm"]["error"] = slf["error"]

    return result
