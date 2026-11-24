"""premieres: something new airs like it would on real tv.

a new episode airs at 8pm on its show's home channel the evening it arrived,
a new film at 9pm; whatever was on is cut at the premiere, like a real
schedule. it's the one thing allowed on top of the guide's frozen day
(core/player/guide_day.py): the day's regular schedule never moves, a
premiere lands on it.

the guide must be the same on every device, so nothing about a premiere is
worked out on a device:

* **the slot** is given here, once, and kept (``player_guide_premieres``).
  a later arrival never moves an earlier premiere.
* **the channel** is named here too, as the channels it could go on, best
  first (the show's network, then its genres, ...). a device puts it on the
  first of those it has. channel ids are made the way the players make them
  (``network-hbo``, ``genre-drama``: webui channels.ts ``slug``).
* **the item** (title, length, artwork) comes from here, so a show that
  arrived an hour ago, which the frozen day's pool has never heard of, still
  premieres tonight.

the slot rule: 8pm (9pm for a film) on the day it arrived when that is at
least half an hour away, so every device has it before it airs; otherwise
the next top of the hour that is, up to 11pm; otherwise tomorrow's 8pm or
9pm. a show's new episodes air back to back, three a night, in order, the
rest on the following evenings. an evening takes a handful of premieres,
not a whole library import: past the cap, a title simply joins the
library without one.

only what is new to the library premieres: an upgrade of something owned
for months is not a premiere.
"""

from __future__ import annotations

import datetime as _dt
import re
import threading
import unicodedata
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.premieres")

HOUR = {"episode": 20, "movie": 21}
LEAD = _dt.timedelta(minutes=30)        # a premiere is announced at least this far ahead
NEW_FOR_DAYS = 3                        # how long after it arrives something can premiere
PER_NIGHT = 3                           # a show's new episodes a night
SHOWS_A_NIGHT = 12                      # an evening's premieres: shows ...
FILMS_A_NIGHT = 6                       # ... and films
KEEP_DAYS = 10
RECENT_FILES = 600

_DDL = """
CREATE TABLE IF NOT EXISTS player_guide_premieres (
    key TEXT PRIMARY KEY,               -- episode:<id> | movie:<id>
    kind TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    show_id INTEGER,
    at_ms INTEGER,                      -- when it airs (epoch ms); NULL = arrived, no premiere
    arrived_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_lock = threading.Lock()
_ready: set = set()


def _ensure(db: Any) -> None:
    key = str(getattr(db, "database_path", None) or getattr(db, "db_path", None) or id(db))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(_DDL)
        conn.commit()
    _ready.add(key)


def slug(text: Any) -> str:
    """a channel id's tail, exactly as the players make it (channels.ts slug):
    lowercase, NFKD, every run of non-word (ascii) characters a dash"""
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    return re.sub(r"[^\w]+", "-", s, flags=re.ASCII).strip("-")


def slot_for(arrived: _dt.datetime, kind: str) -> _dt.datetime:
    """when something that arrived at ``arrived`` (server local time) premieres"""
    hour = HOUR["movie" if kind == "movie" else "episode"]
    day = arrived.replace(minute=0, second=0, microsecond=0)
    prime = day.replace(hour=hour)
    if arrived + LEAD <= prime:
        return prime
    # after its hour: the next top of the hour with the lead, still this evening
    nxt = day + _dt.timedelta(hours=1)
    if arrived + LEAD > nxt:
        nxt += _dt.timedelta(hours=1)
    if nxt.date() == arrived.date() and nxt.hour >= hour:      # 11pm is the last top of the hour
        return nxt
    return (day + _dt.timedelta(days=1)).replace(hour=hour)


def _parse_utc(stamp: Any) -> Optional[_dt.datetime]:
    """a stored 'YYYY-MM-DD HH:MM:SS' (utc, as sqlite's CURRENT_TIMESTAMP
    writes it) as server local time"""
    try:
        s = str(stamp or "").strip().replace("T", " ")[:19]
        utc = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc)
        return utc.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _ms(local: _dt.datetime) -> int:
    return int(local.timestamp() * 1000)


def _kid_safe(rating: Any) -> bool:
    return str(rating or "") in ("G", "PG", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG")


# ── what arrived ─────────────────────────────────────────────────────────────

def arrivals(conn, now: _dt.datetime) -> List[Dict[str, Any]]:
    """the episodes and films new to the library in the last few days, oldest
    arrival first. new means the file AND the title's first file are recent:
    an upgrade of something owned for months is not an arrival."""
    since = now - _dt.timedelta(days=NEW_FOR_DAYS)
    floor = (since - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in conn.execute(
            "SELECT episode_id, movie_id, added_at, runtime_seconds FROM media_files "
            "WHERE video_id IS NULL ORDER BY added_at DESC LIMIT ?", (RECENT_FILES,)):
        at = _parse_utc(r[2])
        if at is None or at < since:
            continue
        key = ("episode", r[0]) if r[0] is not None else ("movie", r[1])
        if key[1] is None or key in seen:
            continue
        seen.add(key)
        out.append({"kind": key[0], "id": int(key[1]), "arrived": at, "file_seconds": r[3]})
    if not out:
        return []
    eps = [a["id"] for a in out if a["kind"] == "episode"]
    movies = [a["id"] for a in out if a["kind"] == "movie"]
    info: Dict[tuple, Dict[str, Any]] = {}
    for chunk in (eps[i:i + 400] for i in range(0, len(eps), 400)):
        for r in conn.execute(
                "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, e.runtime_minutes, "
                "e.added_at, s.title, s.network, s.content_rating, s.runtime_minutes, "
                "s.poster_url IS NOT NULL FROM episodes e JOIN shows s ON s.id=e.show_id "
                "WHERE e.id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            info[("episode", r[0])] = {
                "show_id": r[1], "season": r[2], "episode": r[3], "episode_title": r[4],
                "seconds": (r[5] or r[10] or 0) * 60, "first": r[6], "title": r[7], "network": r[8],
                "rating": r[9], "has_poster": bool(r[11])}
    for chunk in (movies[i:i + 400] for i in range(0, len(movies), 400)):
        for r in conn.execute(
                "SELECT id, title, year, runtime_minutes, added_at, content_rating, studio, "
                "tmdb_collection_id, tmdb_collection_name, poster_url IS NOT NULL FROM movies "
                "WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            info[("movie", r[0])] = {
                "title": r[1], "year": r[2], "seconds": (r[3] or 0) * 60, "first": r[4],
                "rating": r[5], "studio": r[6], "collection": r[8] if r[7] else None,
                "has_poster": bool(r[9])}
    kept = []
    for a in out:
        i = info.get((a["kind"], a["id"]))
        if not i:
            continue
        # the title's own first sighting: old means this file is an upgrade
        if i.get("first") and str(i["first"])[:10] < floor:
            continue
        a.update(i)
        a["seconds"] = int(a.get("file_seconds") or a.get("seconds") or 0)
        kept.append(a)
    kept.sort(key=lambda a: (a["arrived"], a.get("season") or 0, a.get("episode") or 0))
    return kept


def _genres(conn, kind: str, ids: List[int]) -> Dict[int, List[str]]:
    table, col = ("movie_genres", "movie_id") if kind == "movie" else ("show_genres", "show_id")
    out: Dict[int, List[str]] = {}
    for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
        for r in conn.execute(
                f"SELECT l.{col}, g.name FROM {table} l JOIN genres g ON g.id=l.genre_id "  # noqa: S608
                f"WHERE l.{col} IN (%s) ORDER BY l.rowid" % ",".join("?" * len(chunk)), chunk):
            out.setdefault(r[0], []).append(r[1])
    return out


def homes(a: Dict[str, Any], genres: List[str]) -> List[str]:
    """the channels a premiere could air on, best first, by the ids the
    players give their channels"""
    out: List[str] = []
    if a["kind"] == "episode":
        if a.get("network"):
            out.append("network-" + slug(a["network"]))
    else:
        if a.get("collection"):
            out.append("collection-" + slug(a["collection"]))
        if a.get("studio"):
            out.append("studio-" + slug(a["studio"]))
    out += ["genre-" + slug(g) for g in genres if slug(g)]
    if _kid_safe(a.get("rating")):
        out.append("kids")
    out.append("type-tv" if a["kind"] == "episode" else "type-movies")
    return list(dict.fromkeys(out))


# ── the slots, given once ────────────────────────────────────────────────────

def assign(db, found: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """give every arrival not seen before its slot, and return every known
    slot ``{key: at_ms | None}``. a slot once given is never changed."""
    _ensure(db)
    with _lock:
        with db._get_connection() as conn:
            known = {r[0]: r[1] for r in conn.execute("SELECT key, at_ms FROM player_guide_premieres")}
            # what each evening already holds: {day: {show_id: count}}, {day: films}
            per_show: Dict[str, Dict[int, int]] = {}
            films: Dict[str, int] = {}
            for r in conn.execute("SELECT kind, show_id, at_ms FROM player_guide_premieres "
                                  "WHERE at_ms IS NOT NULL"):
                day = _dt.datetime.fromtimestamp(r[2] / 1000).date().isoformat()
                if r[0] == "movie":
                    films[day] = films.get(day, 0) + 1
                else:
                    per_show.setdefault(day, {})[r[1]] = per_show.get(day, {}).get(r[1], 0) + 1
            first_slot: Dict[tuple, _dt.datetime] = {}
            for a in found:
                key = "%s:%s" % (a["kind"], a["id"])
                if key in known:
                    continue
                at: Optional[_dt.datetime] = None
                if a["seconds"] >= 60:
                    at = slot_for(a["arrived"], a["kind"])
                    if a["kind"] == "movie":
                        day = at.date().isoformat()
                        if films.get(day, 0) >= FILMS_A_NIGHT:
                            at = None
                        else:
                            films[day] = films.get(day, 0) + 1
                    else:
                        # a show's batch shares the slot its first episode got,
                        # three a night, the rest on the next evenings
                        show = a["show_id"]
                        at = first_slot.setdefault((show, at.date()), at)
                        for _ in range(60):
                            day = at.date().isoformat()
                            shows = per_show.setdefault(day, {})
                            if shows.get(show, 0) < PER_NIGHT and (
                                    show in shows or len(shows) < SHOWS_A_NIGHT):
                                shows[show] = shows.get(show, 0) + 1
                                break
                            if show not in shows and len(shows) >= SHOWS_A_NIGHT:
                                at = None
                                break
                            at = (at + _dt.timedelta(days=1)).replace(
                                hour=HOUR["episode"], minute=0, second=0, microsecond=0)
                at_ms = _ms(at) if at is not None else None
                conn.execute(
                    "INSERT OR IGNORE INTO player_guide_premieres (key, kind, item_id, show_id, at_ms, "
                    "arrived_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, a["kind"], a["id"], a.get("show_id"), at_ms,
                     a["arrived"].strftime("%Y-%m-%d %H:%M:%S")))
                known[key] = at_ms
            conn.execute("DELETE FROM player_guide_premieres WHERE created_at < datetime('now', ?)",
                         ("-%d days" % KEEP_DAYS,))
            conn.commit()
    return known


def listing(db, video_conn, *, now: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    """the premieres from yesterday on, soonest first, each ready to air:
    ``{key, kind, id, show_id, title, subtitle, seconds, at, homes,
    has_poster, rating}``. ``at`` is epoch milliseconds."""
    now = now or _dt.datetime.now()
    found = arrivals(video_conn, now)
    slots = assign(db, found)
    floor = _ms(now.replace(hour=0, minute=0, second=0, microsecond=0) - _dt.timedelta(days=1))
    live = [a for a in found if (slots.get("%s:%s" % (a["kind"], a["id"])) or 0) >= floor]
    genres = {"movie": _genres(video_conn, "movie", [a["id"] for a in live if a["kind"] == "movie"]),
              "episode": _genres(video_conn, "episode", list({a["show_id"] for a in live
                                                              if a["kind"] == "episode"}))}
    out = []
    for a in live:
        key = "%s:%s" % (a["kind"], a["id"])
        if a["kind"] == "episode":
            sub = "S%s E%s%s" % (a["season"], a["episode"],
                                 (" · " + a["episode_title"]) if a.get("episode_title") else "")
            g = genres["episode"].get(a["show_id"], [])
        else:
            sub = str(a["year"]) if a.get("year") else None
            g = genres["movie"].get(a["id"], [])
        out.append({"key": key, "kind": a["kind"], "id": a["id"], "show_id": a.get("show_id"),
                    "title": a["title"], "subtitle": sub, "seconds": a["seconds"],
                    "at": slots[key], "homes": homes(a, g), "has_poster": a["has_poster"],
                    "rating": a.get("rating")})
    # two at the same time air back to back in this order: a show's episodes in their own
    order = {"%s:%s" % (a["kind"], a["id"]): (a.get("season") or 0, a.get("episode") or 0) for a in live}
    out.sort(key=lambda p: (p["at"], p["title"] or "", order[p["key"]], p["key"]))
    return out
