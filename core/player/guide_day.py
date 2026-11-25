"""the guide's pool, frozen for the day.

every device builds a channel's day from the same inputs (channel id, the
day, the pool, the commercial clips), so every device shows the same thing
at the same moment. the pool was the live catalog: a movie that finished
downloading at 3pm, a new episode in the sample, or a clip the commercial
cache just fetched re-seeded the whole day, so "on now" jumped on the guide
and devices tuned before the change kept their old queue.

the guide now asks for its pool with ?guide=1 and gets the one taken the
first time anyone asked today (per view: what a kids profile sees is its
own pool). tomorrow takes a new one. the rest of the player (home rails,
browsing) keeps reading the live catalog.

kept in player.db so a restart mid-day doesn't re-seed the day either.
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
from typing import Any, Callable, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS player_guide_day (
    view_key TEXT NOT NULL,
    day TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (view_key, day)
)
"""

_lock = threading.Lock()
_ready: set = set()


def today(now: Optional[_dt.datetime] = None) -> str:
    """the server's local date: the house's devices share its clock"""
    return (now or _dt.datetime.now()).date().isoformat()


def zone_name() -> Optional[str]:
    """the server's timezone by its IANA name ("America/Los_Angeles"), or
    None when the machine doesn't say. a name, not an offset: the offset
    changes twice a year and a device has to lay out other days too."""
    import os
    candidates = [os.environ.get("TZ")]
    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            candidates.append(fh.read().strip())
    except OSError:
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        import tzlocal  # windows, and anywhere the files above are missing
        candidates.append(str(tzlocal.get_localzone_name() or ""))
    except Exception:  # noqa: BLE001, S110 - not installed: the offset below still answers
        pass
    for name in candidates:
        name = str(name or "").strip().lstrip(":")
        if not name or "/" not in name and name.upper() not in ("UTC", "GMT"):
            continue
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(name)
            return name
        except Exception:  # noqa: BLE001, S112 - not a zone this machine knows
            continue
    return None


def clock(now: Optional[_dt.datetime] = None) -> dict:
    """the clock the guide runs on: the server's.

    every device lays the guide out from "the start of today" and seeds each
    channel by the date. they each used their OWN midnight and their own
    date, so a phone in another timezone (or a server whose TZ wasn't the
    house's) had a different guide from the tv in the living room. the server
    hands its clock out with the day's pool, and every device counts days by
    it; what a device shows is still its own local time for those instants.

    ``tz`` is the zone's name when the machine says; ``offset_minutes`` is
    the server's offset from utc right now, the fallback when it doesn't."""
    local = (now or _dt.datetime.now()).astimezone()
    offset = local.utcoffset() or _dt.timedelta(0)
    return {"tz": zone_name(), "offset_minutes": int(offset.total_seconds() // 60),
            "day": today(now)}


def _ensure(db: Any) -> None:
    # by the file, not the object: a new object can reuse a dead one's id()
    key = str(getattr(db, "database_path", None) or getattr(db, "db_path", None) or id(db))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(_DDL)
        conn.commit()
    _ready.add(key)


def frozen(db: Any, view_key: str, make: Callable[[], Any], *, day: Optional[str] = None,
           keep: Callable[[Any], bool] = bool) -> Any:
    """today's value for ``view_key``: the stored one, else ``make()`` stored
    for the rest of the day. older days are dropped as a new one starts.
    a value ``keep`` refuses (an empty catalog while the server starts, no
    clips yet while the cache warms) is handed out but not frozen."""
    day = day or today()
    _ensure(db)
    with _lock:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT payload FROM player_guide_day WHERE view_key = ? AND day = ?",
                (view_key, day),
            ).fetchone()
            if row:
                return json.loads(row[0])
            value = make()
            if not keep(value):
                return value
            conn.execute(
                "INSERT OR REPLACE INTO player_guide_day (view_key, day, payload) VALUES (?, ?, ?)",
                (view_key, day, json.dumps(value, separators=(",", ":"))),
            )
            conn.execute("DELETE FROM player_guide_day WHERE day < ?", (day,))
            conn.commit()
            return value
