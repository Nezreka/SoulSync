"""The calendar's iCal feed, subscribable from Google, Apple or Outlook.

Those apps fetch the URL on their own servers, with no cookie, so the old
``/api/video/calendar.ics`` answered them 401 on any install with more than one
profile, a login or a launch PIN. The feed now carries its own credential: a
per-profile token in the path, like Sonarr's apikey or SoulSync's invite links.
Every gate leaves a well-formed feed path open and the route itself refuses a
token it doesn't know, so the path being open grants nothing on its own.

Pure apart from ``new_token``: the route does the db and profile reads.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Dict, Iterable, Optional

FEED_PREFIX = "/api/video/calendar/feed/"
_FEED_PATH = re.compile(r"/api/video/calendar/feed/[A-Za-z0-9_-]{32,64}\.ics")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,64}")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_feed_path(path: str, method: str) -> bool:
    """A GET for a well-formed feed url. Only the shape is checked here."""
    return (method or "GET").upper() == "GET" and _FEED_PATH.fullmatch(path or "") is not None


def valid_token_shape(token: str) -> bool:
    return _TOKEN.fullmatch(token or "") is not None


def profile_may_read_feed(profile: Optional[Dict[str, Any]]) -> bool:
    """The token's profile still exists and still has the video side."""
    if not profile:
        return False
    if profile.get("is_admin") or profile.get("id") == 1:
        return True
    return (profile.get("allowed_sides") or "music") in ("video", "both")


def _esc(t: Any) -> str:
    return (str(t or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


_MOVIE_LABEL = {"cinema": "In Cinemas", "available": "Home Release"}


def build_ics(episodes: Iterable[Dict[str, Any]], movies: Iterable[Dict[str, Any]] = ()) -> str:
    """The VCALENDAR body. A real air time (TVDB, network-local) becomes a timed
    event in floating local time, the convention the page uses; the 00:00
    placeholder means streaming/unknown and stays all-day. Movies are all-day."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//SoulSync//Video Calendar//EN",
             "X-WR-CALNAME:SoulSync Airings", "CALSCALE:GREGORIAN"]
    for e in episodes or ():
        day = str(e.get("air_date") or "")[:10].replace("-", "")
        if len(day) != 8:
            continue
        code = "S%02dE%02d" % (e.get("season_number") or 0, e.get("episode_number") or 0)
        summary = "%s %s" % (e.get("show_title") or "?", code)
        if e.get("title"):
            summary += " — " + e["title"]
        airs = str(e.get("airs_time") or "")[:5]
        if len(airs) == 5 and airs[2] == ":" and airs != "00:00":
            dtstart = "DTSTART:%sT%s%s00" % (day, airs[:2], airs[3:5])
        else:
            dtstart = "DTSTART;VALUE=DATE:" + day
        lines += ["BEGIN:VEVENT",
                  "UID:ss-%s-%s-%s@soulsync" % (e.get("show_tmdb_id") or e.get("show_id"),
                                                e.get("season_number"), e.get("episode_number")),
                  dtstart,
                  "SUMMARY:" + _esc(summary),
                  "DESCRIPTION:" + _esc(e.get("overview") or ""),
                  "STATUS:CONFIRMED", "END:VEVENT"]
    for m in movies or ():
        day = str(m.get("date") or "").replace("-", "")
        if len(day) != 8:
            continue
        title = m.get("title") or "?"
        if m.get("year"):
            title += " (%s)" % m["year"]
        lines += ["BEGIN:VEVENT",
                  "UID:ss-movie-%s-%s@soulsync" % (m.get("tmdb_id"), m.get("type")),
                  "DTSTART;VALUE=DATE:" + day,
                  "SUMMARY:" + _esc("%s — %s" % (title, _MOVIE_LABEL.get(m.get("type"), "Release"))),
                  "STATUS:CONFIRMED", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


__all__ = ["FEED_PREFIX", "new_token", "is_feed_path", "valid_token_shape",
           "profile_may_read_feed", "build_ics"]
