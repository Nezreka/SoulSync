"""player notifications: what each profile is told, and how it wants telling.

three tables in player.db:

  * player_notifications: what was delivered. the app and the web player
    read it from a last-seen id, so ids only grow in delivery order
  * player_notification_pending: held back by quiet hours or the daily
    digest. a held one gets its id when it is let out, so a device reading
    "since my last id" never skips it
  * player_notification_prefs: one json blob per profile

the generator (notify_gen.py) and the event hooks decide what happened;
deliver() decides whether and when the profile hears about it. reads never
make tables: a server nobody notified answers empty.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

TABLE = "player_notifications"
PENDING = "player_notification_pending"
PREFS = "player_notification_prefs"

KEEP_PER_PROFILE = 300
KEEP_DAYS = 90
MAX_LIMIT = 200


class PrefsError(ValueError):
    pass


# id, label, help, default, extras (locked: can't be turned off; device: where it shows)
KINDS: Tuple[Tuple[str, str, str, bool, Dict[str, Any]], ...] = (
    ("new_episode", "New episodes",
     "A new episode of a show you're watching is ready. One a day per show.", True, {}),
    ("featured_movie", "Featured films",
     "A well-reviewed film arrives. Never more than 3 a week.", True, {}),
    ("watchlist_available", "From your watchlist",
     "Something on your watchlist is ready to play.", True, {}),
    ("request_done", "Your requests",
     "Something you asked for is ready, or couldn't be found.", True, {}),
    ("season_premiere", "Season premieres",
     "A new season of a show you've watched starts soon, or is here.", True, {}),
    ("leaving_soon", "Leaving soon",
     "Something you started is about to be removed from the server.", False, {}),
    ("download_ready", "Downloads ready",
     "A smart download finished on your phone or tablet.", True, {"device": "phone"}),
    ("party_invite", "Watch party invites",
     "Someone asked you to watch with them.", True, {}),
    ("admin_message", "Messages from the admin",
     "Notes from whoever runs this server. These always come through.", True, {"locked": True}),
    ("new_music", "New music",
     "A new album from an artist you play a lot.", False, {}),
    ("home_memories", "Memories",
     "Home videos from this day in past years, once a day.", False, {}),
)
KIND_IDS = tuple(k[0] for k in KINDS)
# made by the server itself, never a switch
SYSTEM_KINDS = ("digest", "test")
# straight through, whatever the digest says
NEVER_DIGEST = ("admin_message", "party_invite", "test", "digest")
# straight through, even in quiet hours
NEVER_QUIET = ("admin_message", "test")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT_PREFS: Dict[str, Any] = {
    "kinds": {k[0]: k[3] for k in KINDS},
    "featured_not_in_library": False,
    "memories_time": "09:00",
    "quiet_hours": False,
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "digest": False,
    "digest_time": "18:00",
    "timezone": "",
}

_ready: set = set()
_lock = threading.Lock()


# ── schema ───────────────────────────────────────────────────────────────

def _key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


def ensure_schema(db: Any) -> None:
    key = _key(db)
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                image TEXT,
                link TEXT,
                data_json TEXT,
                created_at REAL NOT NULL,
                read_at REAL,
                group_key TEXT
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_profile ON {TABLE} (profile_id, id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_group ON {TABLE} (profile_id, group_key)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_kind ON {TABLE} (profile_id, kind, created_at)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {PENDING} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                image TEXT,
                link TEXT,
                data_json TEXT,
                created_at REAL NOT NULL,
                group_key TEXT,
                release_at REAL NOT NULL,
                via TEXT NOT NULL
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {PENDING}_due ON {PENDING} (release_at)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {PENDING}_group ON {PENDING} (profile_id, group_key)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {PREFS} (
                profile_id INTEGER PRIMARY KEY,
                prefs_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""")
        conn.commit()
    _ready.add(key)


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                        (table,)).fetchone() is not None


# ── prefs ────────────────────────────────────────────────────────────────

def _bool(name: str, v: Any) -> bool:
    if isinstance(v, bool):
        return v
    raise PrefsError(f"{name}: expected true or false")


def _hhmm(name: str, v: Any) -> str:
    if isinstance(v, str) and _HHMM.match(v.strip()):
        return v.strip()
    raise PrefsError(f"{name}: expected a time like 21:30")


def _tz(v: Any) -> str:
    if v is None or v == "":
        return ""
    if not isinstance(v, str) or len(v) > 64:
        raise PrefsError("timezone: expected a zone name like America/Chicago")
    name = v.strip()
    if _zone(name) is None:
        raise PrefsError("timezone: expected a zone name like America/Chicago")
    return name


def defaults() -> Dict[str, Any]:
    out = dict(DEFAULT_PREFS)
    out["kinds"] = dict(DEFAULT_PREFS["kinds"])
    return out


def clean_prefs(changes: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``base`` with ``changes`` laid over it, every value checked. an unknown
    key or a bad value refuses the whole change."""
    if not isinstance(changes, dict) or not changes:
        raise PrefsError("nothing to change")
    out = defaults() if base is None else {**base, "kinds": dict(base.get("kinds") or {})}
    for k, v in changes.items():
        if k == "kinds":
            if not isinstance(v, dict):
                raise PrefsError("kinds: expected {kind: true or false}")
            for kind, on in v.items():
                if kind not in KIND_IDS:
                    raise PrefsError(f"no notification called {kind}")
                on = _bool(kind, on)
                if kind == "admin_message" and not on:
                    raise PrefsError("messages from the admin can't be turned off")
                out["kinds"][kind] = on
        elif k in ("featured_not_in_library", "quiet_hours", "digest"):
            out[k] = _bool(k, v)
        elif k in ("memories_time", "quiet_start", "quiet_end", "digest_time"):
            out[k] = _hhmm(k, v)
        elif k == "timezone":
            out[k] = _tz(v)
        else:
            raise PrefsError(f"no notification setting called {k}")
    return out


def _read_prefs(raw: Optional[str]) -> Dict[str, Any]:
    """what was saved, over the defaults; anything an older version wrote
    that no longer checks out falls back to its default"""
    out = defaults()
    try:
        saved = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return out
    if not isinstance(saved, dict):
        return out
    for k, v in saved.items():
        try:
            if k == "kinds" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk in KIND_IDS and isinstance(vv, bool)}
            out = clean_prefs({k: v}, out)
        except PrefsError:
            continue
    out["kinds"]["admin_message"] = True
    return out


def get_prefs(db: Any, profile_id: int) -> Dict[str, Any]:
    with db._get_connection() as conn:
        if not _has(conn, PREFS):
            return defaults()
        row = conn.execute(f"SELECT prefs_json FROM {PREFS} WHERE profile_id = ?",
                           (int(profile_id),)).fetchone()
    return _read_prefs(row[0] if row else None)


def all_prefs(db: Any) -> Dict[int, Dict[str, Any]]:
    """every profile that saved prefs (the rest are on the defaults)"""
    with db._get_connection() as conn:
        if not _has(conn, PREFS):
            return {}
        rows = conn.execute(f"SELECT profile_id, prefs_json FROM {PREFS}").fetchall()
    return {int(r[0]): _read_prefs(r[1]) for r in rows}


def put_prefs(db: Any, profile_id: int, changes: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    out = clean_prefs(changes, get_prefs(db, profile_id))
    ensure_schema(db)
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO {PREFS} (profile_id, prefs_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(profile_id) DO UPDATE SET prefs_json = excluded.prefs_json, "
            "updated_at = excluded.updated_at",
            (int(profile_id), json.dumps(out), t))
        # what was held for a digest or quiet hours that are now off goes at
        # the next tick; a kind turned off is dropped there
        if not out["digest"]:
            conn.execute(f"UPDATE {PENDING} SET release_at = ? WHERE profile_id = ? AND via = 'digest'",
                         (t, int(profile_id)))
        if not out["quiet_hours"]:
            conn.execute(f"UPDATE {PENDING} SET release_at = ? WHERE profile_id = ? AND via = 'quiet'",
                         (t, int(profile_id)))
        conn.commit()
    return out


def kinds_catalogue() -> List[Dict[str, Any]]:
    return [{"id": k, "label": label, "help": help_, "default": default, **extra}
            for k, label, help_, default, extra in KINDS]


def wants(prefs: Dict[str, Any], kind: str) -> bool:
    if kind in SYSTEM_KINDS or kind == "admin_message":
        return True
    return bool((prefs.get("kinds") or {}).get(kind, False))


# ── clocks ───────────────────────────────────────────────────────────────

def _zone(name: str):
    if not name:
        return None
    if name.upper() in ("UTC", "GMT"):
        return _dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a zone this machine doesn't know
        return None


def local_now(prefs: Dict[str, Any], now: float) -> _dt.datetime:
    """the profile's wall clock: its zone, else the server's"""
    zone = _zone(prefs.get("timezone") or "")
    if zone is None:
        return _dt.datetime.fromtimestamp(now).astimezone()
    return _dt.datetime.fromtimestamp(now, tz=zone)


def local_day(prefs: Dict[str, Any], now: float) -> str:
    return local_now(prefs, now).date().isoformat()


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _next_at(prefs: Dict[str, Any], now: float, hhmm: str) -> float:
    """the next time the profile's clock reads hhmm (now, if it does)"""
    local = local_now(prefs, now)
    h, m = (int(x) for x in hhmm.split(":"))
    at = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if at.timestamp() < now - 60:
        at = at + _dt.timedelta(days=1)
    return at.timestamp()


def in_quiet(prefs: Dict[str, Any], now: float) -> bool:
    if not prefs.get("quiet_hours"):
        return False
    start, end = _minutes(prefs["quiet_start"]), _minutes(prefs["quiet_end"])
    if start == end:
        return False
    local = local_now(prefs, now)
    m = local.hour * 60 + local.minute
    return start <= m < end if start < end else (m >= start or m < end)


def quiet_ends(prefs: Dict[str, Any], now: float) -> float:
    return _next_at(prefs, now, prefs["quiet_end"])


def is_time(prefs: Dict[str, Any], now: float, hhmm: str) -> bool:
    """the profile's clock is at or past hhmm today"""
    local = local_now(prefs, now)
    return local.hour * 60 + local.minute >= _minutes(hhmm)


# ── delivery ─────────────────────────────────────────────────────────────

def _clip(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _insert(conn, profile_id: int, kind: str, title: str, body: str, image: Optional[str],
            link: Optional[str], data: Optional[dict], group_key: Optional[str], t: float) -> int:
    cur = conn.execute(
        f"INSERT INTO {TABLE} (profile_id, kind, title, body, image, link, data_json, created_at, group_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (profile_id, kind, title, body, image, link, json.dumps(data) if data else None, t, group_key))
    return int(cur.lastrowid)


def deliver(db: Any, profile_id: int, kind: str, title: str, body: str = "", *,
            image: Optional[str] = None, link: Optional[str] = None,
            data: Optional[dict] = None, group_key: Optional[str] = None,
            replace: bool = False, prefs: Optional[Dict[str, Any]] = None,
            now: Optional[float] = None) -> Optional[str]:
    """tell one profile one thing, if it wants to hear it.

    returns "sent", "held" (quiet hours or the digest) or None (turned off,
    or already told: the same group_key). ``replace`` swaps an earlier note
    with the same group_key for this one (3 new episodes, not three notes);
    it goes out again, as new, under a new id."""
    if kind not in KIND_IDS and kind not in SYSTEM_KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    pid = int(profile_id)
    p = prefs if prefs is not None else get_prefs(db, pid)
    if not wants(p, kind):
        return None
    t = time.time() if now is None else now
    title = _clip(title, 200) or "SoulSync"
    body = _clip(body, 500)
    link = link if isinstance(link, str) and link.startswith("/player") else None
    ensure_schema(db)
    with _lock, db._get_connection() as conn:
        if group_key:
            seen = conn.execute(f"SELECT id FROM {TABLE} WHERE profile_id = ? AND group_key = ?",
                                (pid, group_key)).fetchall()
            held = conn.execute(f"SELECT id FROM {PENDING} WHERE profile_id = ? AND group_key = ?",
                                (pid, group_key)).fetchall()
            if (seen or held) and not replace:
                return None
            if held:
                conn.execute(f"DELETE FROM {PENDING} WHERE profile_id = ? AND group_key = ?", (pid, group_key))
            if seen:
                conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ? AND group_key = ?", (pid, group_key))
        via = None
        if p.get("digest") and kind not in NEVER_DIGEST:
            via, release = "digest", _next_at(p, t, p["digest_time"])
        elif kind not in NEVER_QUIET and in_quiet(p, t):
            via, release = "quiet", quiet_ends(p, t)
        if via:
            conn.execute(
                f"INSERT INTO {PENDING} (profile_id, kind, title, body, image, link, data_json, created_at, "
                "group_key, release_at, via) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, kind, title, body, image, link, json.dumps(data) if data else None, t,
                 group_key, release, via))
            conn.commit()
            return "held"
        _insert(conn, pid, kind, title, body, image, link, data, group_key, t)
        conn.commit()
    return "sent"


def _digest_body(rows: List[Any]) -> str:
    bits = [r["title"] for r in rows[:4]]
    more = len(rows) - len(bits)
    return " · ".join(bits) + (f" · and {more} more" if more > 0 else "")


def release_due(db: Any, *, now: Optional[float] = None,
                prefs_of: Optional[Dict[int, Dict[str, Any]]] = None) -> int:
    """let out what quiet hours or the digest held, when its time comes.
    a digest is one note for the day; quiet-hour ones go out as they were.
    returns how many notes went out"""
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        if not _has(conn, PENDING):
            return 0
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM {PENDING} WHERE release_at <= ? ORDER BY id", (t,)).fetchall()
    if not rows:
        return 0
    saved = prefs_of if prefs_of is not None else all_prefs(db)
    by_profile: Dict[int, List[Any]] = {}
    for r in rows:
        by_profile.setdefault(int(r["profile_id"]), []).append(r)
    sent = 0
    with _lock, db._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        for pid, held in by_profile.items():
            p = saved.get(pid) or defaults()
            live = [r for r in held if wants(p, r["kind"])]
            dropped = [r["id"] for r in held if not wants(p, r["kind"])]
            if dropped:
                conn.executemany(f"DELETE FROM {PENDING} WHERE id = ?", [(i,) for i in dropped])
            if not live:
                continue
            if in_quiet(p, t):
                conn.executemany(f"UPDATE {PENDING} SET release_at = ? WHERE id = ?",
                                 [(quiet_ends(p, t), r["id"]) for r in live])
                continue
            digest = [r for r in live if r["via"] == "digest" and p.get("digest")]
            single = [r for r in live if r not in digest]
            for r in single:
                _insert(conn, pid, r["kind"], r["title"], r["body"], r["image"], r["link"],
                        json.loads(r["data_json"]) if r["data_json"] else None, r["group_key"], t)
                sent += 1
            if len(digest) == 1:
                r = digest[0]
                _insert(conn, pid, r["kind"], r["title"], r["body"], r["image"], r["link"],
                        json.loads(r["data_json"]) if r["data_json"] else None, r["group_key"], t)
                sent += 1
            elif digest:
                items = [{"kind": r["kind"], "title": r["title"], "body": r["body"], "link": r["link"],
                          "image": r["image"]} for r in digest]
                image = next((r["image"] for r in digest if r["image"]), None)
                _insert(conn, pid, "digest", f"{len(digest)} things new for you today",
                        _digest_body(digest), image, "/player", {"items": items},
                        f"digest:{local_day(p, t)}", t)
                sent += 1
            conn.executemany(f"DELETE FROM {PENDING} WHERE id = ?", [(r["id"],) for r in live])
        conn.commit()
    return sent


# ── reading and tidying ──────────────────────────────────────────────────

def _shape(r) -> Dict[str, Any]:
    return {"id": r[0], "kind": r[1], "title": r[2], "body": r[3], "image": r[4], "link": r[5],
            "created_at": r[6], "read": r[7] is not None, "group_key": r[8]}


def list_for(db: Any, profile_id: int, *, since: int = 0, limit: int = 50) -> Dict[str, Any]:
    """newest first. ``since``: only ids above it (a device catching up)"""
    limit = max(1, min(int(limit), MAX_LIMIT))
    pid = int(profile_id)
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return {"notifications": [], "unread": 0, "latest_id": 0}
        rows = conn.execute(
            f"SELECT id, kind, title, body, image, link, created_at, read_at, group_key FROM {TABLE} "
            "WHERE profile_id = ? AND id > ? ORDER BY id DESC LIMIT ?",
            (pid, max(0, int(since)), limit)).fetchall()
        unread = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE profile_id = ? AND read_at IS NULL",
                              (pid,)).fetchone()[0]
        latest = conn.execute(f"SELECT MAX(id) FROM {TABLE} WHERE profile_id = ?", (pid,)).fetchone()[0]
    return {"notifications": [_shape(r) for r in rows], "unread": int(unread), "latest_id": int(latest or 0)}


def mark_read(db: Any, profile_id: int, ids: Optional[Iterable[Any]] = None, *, all_: bool = False,
              now: Optional[float] = None) -> int:
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return 0
        if all_:
            n = conn.execute(f"UPDATE {TABLE} SET read_at = ? WHERE profile_id = ? AND read_at IS NULL",
                             (t, int(profile_id))).rowcount
        else:
            clean = []
            for i in ids or []:
                if isinstance(i, bool):
                    continue
                try:
                    clean.append(int(i))
                except (TypeError, ValueError):
                    continue
            clean = clean[:500]
            if not clean:
                return 0
            marks = ",".join("?" * len(clean))
            n = conn.execute(f"UPDATE {TABLE} SET read_at = ? WHERE profile_id = ? AND read_at IS NULL "  # noqa: S608
                             f"AND id IN ({marks})", (t, int(profile_id), *clean)).rowcount
        conn.commit()
    return int(n)


def remove(db: Any, profile_id: int, notification_id: int) -> bool:
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return False
        n = conn.execute(f"DELETE FROM {TABLE} WHERE id = ? AND profile_id = ?",
                         (int(notification_id), int(profile_id))).rowcount
        conn.commit()
    return n > 0


def count_since(db: Any, profile_id: int, kind: str, since: float) -> int:
    """how many of a kind the profile was told (or is being held for) since
    a time: the weekly cap on featured films reads this"""
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return 0
        n = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE profile_id = ? AND kind = ? AND created_at >= ?",
                         (int(profile_id), kind, since)).fetchone()[0]
        if _has(conn, PENDING):
            n += conn.execute(f"SELECT COUNT(*) FROM {PENDING} WHERE profile_id = ? AND kind = ? "
                              "AND created_at >= ?", (int(profile_id), kind, since)).fetchone()[0]
    return int(n)


def told(db: Any, profile_id: int, group_key: str) -> bool:
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return False
        if conn.execute(f"SELECT 1 FROM {TABLE} WHERE profile_id = ? AND group_key = ?",
                        (int(profile_id), group_key)).fetchone():
            return True
        return bool(_has(conn, PENDING) and conn.execute(
            f"SELECT 1 FROM {PENDING} WHERE profile_id = ? AND group_key = ?",
            (int(profile_id), group_key)).fetchone())


def prune(db: Any, *, now: Optional[float] = None) -> int:
    """old notes go after KEEP_DAYS, and a profile keeps its newest
    KEEP_PER_PROFILE"""
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        if not _has(conn, TABLE):
            return 0
        n = conn.execute(f"DELETE FROM {TABLE} WHERE created_at < ?", (t - KEEP_DAYS * 86400,)).rowcount
        n += conn.execute(
            f"DELETE FROM {TABLE} WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
            f"(PARTITION BY profile_id ORDER BY id DESC) AS rn FROM {TABLE}) WHERE rn > ?)",
            (KEEP_PER_PROFILE,)).rowcount
        conn.commit()
    return int(n)


# ── event hooks ──────────────────────────────────────────────────────────
# each is cheap and never raises: a note is never worth failing the action

def _player_db():
    from database.player_database import get_player_database
    return get_player_database()


REQUEST_LINKS = ("video-requests", "requests")


def request_outcome(message: str, kind: str) -> Optional[str]:
    """"ready" or "refused" for a request note worth a ping; None for the
    steps between (approved, on the way)"""
    text = str(message or "").lower()
    if "in your library" in text:
        return "ready"
    if "declined" in text or "couldn't" in text or "failed" in text or kind == "error":
        return "refused"
    return None


def on_profile_note(profile_id: int, message: str, kind: str, link: Optional[str]) -> None:
    """profile_notify's listener: a request that is ready, or refused"""
    if link not in REQUEST_LINKS:
        return
    outcome = request_outcome(message, kind)
    if outcome is None:
        return
    try:
        title = "Your request is ready" if outcome == "ready" else "About your request"
        deliver(_player_db(), int(profile_id), "request_done", title, message,
                link="/player", data={"outcome": outcome})
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("player.notifications").debug("request note failed", exc_info=True)


def admin_message(profile_id: int, text: str, header: str = "") -> None:
    """a message an admin sent from Server Activity"""
    try:
        deliver(_player_db(), int(profile_id), "admin_message", header or "A message from your server",
                text, link="/player")
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("player.notifications").debug("admin message note failed", exc_info=True)


def party_invites(db: Any, party: Dict[str, Any], host_name: str, profile_ids: Iterable[int], *,
                  now: Optional[float] = None) -> int:
    """tell each invited profile about a watch party (never the host)"""
    sent = 0
    host = party.get("host_profile_id")
    title = str(party.get("title") or "a watch party")
    when = party.get("scheduled_for")
    if when:
        try:
            at = _dt.datetime.fromtimestamp(float(when)).strftime("%a %H:%M")
            body = f"{title}, {at}. Tap to RSVP."
        except (TypeError, ValueError, OSError):
            body = f"{title}. Tap to join."
    else:
        body = f"{title}. It's starting now."
    for pid in dict.fromkeys(profile_ids):
        try:
            if int(pid) == int(host or 0):
                continue
            if deliver(db, int(pid), "party_invite", f"{host_name} invited you to watch", body,
                       link=f"/player/watching?party={party.get('id')}", group_key=f"party:{party.get('id')}", now=now):
                sent += 1
        except (TypeError, ValueError):
            continue
    return sent
