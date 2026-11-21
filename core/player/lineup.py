"""the guide's channels and how each profile arranges them, in player.db.

two kinds of channel a person makes (the automatic ones, made from the
library, stay the guide's own):
  * the server's: an admin makes them, everyone gets them
  * a profile's own: only that profile sees them
both are a spec: what goes on the channel (see clean_spec). the client
schedules them from its catalog, like the automatic ones.

and each profile's lineup: the order its channels sit in, which it hid, and
what it renamed. by channel id, so it covers the automatic channels too. a
channel the lineup doesn't name sits where the guide puts it.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

CHANNELS = "player_channels"
LINEUP = "player_lineup"
MAX_CHANNELS = 100          # per owner (a profile, or the server)
MAX_LINEUP = 500
NAME_MAX = 40

_ready: set = set()


class LineupError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {CHANNELS} (
                id TEXT PRIMARY KEY,
                owner_profile_id INTEGER,          -- NULL: the server's, for everyone
                spec_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {CHANNELS}_owner ON {CHANNELS} (owner_profile_id)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {LINEUP} (
                profile_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                name TEXT,
                PRIMARY KEY (profile_id, channel_id)
            )""")
        conn.commit()
    _ready.add(key)


# ── a channel's spec ─────────────────────────────────────────────────────

KINDS = ("movies", "tv")
ORDERS = ("shuffle", "in_order")


def _str_list(v: Any, name: str, limit: int = 30, each: int = 80) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > limit:
        raise LineupError(f"{name} must be a list of up to {limit}")
    out = []
    for x in v:
        if not isinstance(x, str) or not x.strip():
            raise LineupError(f"{name} must be words")
        out.append(x.strip()[:each])
    return list(dict.fromkeys(out))


def _int(v: Any, name: str, lo: int, hi: int) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise LineupError(f"{name} must be a number from {lo} to {hi}")
    return int(v)


def clean_spec(raw: Any) -> Dict[str, Any]:
    """what goes on a channel. every field is optional but the name and at
    least one kind (or a show for a marathon):
      name, kinds [movies, tv], genres (any of), decades (any of, e.g. 1990),
      studios / networks (any of), collection, min_score (0-10), kids,
      added_days (added in the last n days), show (one show, start to
      finish), people ([{id, name}]: their films and shows), order
      (shuffle or in_order)"""
    if not isinstance(raw, dict):
        raise LineupError("a channel needs a spec")
    name = " ".join(str(raw.get("name") or "").split())[:NAME_MAX]
    if not name:
        raise LineupError("give the channel a name")
    show = _int(raw.get("show"), "show", 1, 2**31)
    kinds = [k for k in _str_list(raw.get("kinds"), "kinds", 2) if k in KINDS]
    people = _people(raw.get("people"))
    if not kinds and not show:
        raise LineupError("pick movies, tv, or a show")
    decades = [_int(d, "decades", 1900, 2100) for d in (raw.get("decades") or [])]
    if not isinstance(raw.get("decades") or [], list) or len(decades) > 20:
        raise LineupError("decades must be a list")
    min_score = raw.get("min_score")
    if min_score is not None and (isinstance(min_score, bool) or not isinstance(min_score, (int, float))
                                  or not 0 <= min_score <= 10):
        raise LineupError("min_score must be from 0 to 10")
    order = raw.get("order") or ("in_order" if show else "shuffle")
    if order not in ORDERS:
        raise LineupError("order is shuffle or in_order")
    collection = raw.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise LineupError("collection must be words")
    spec = {
        "name": name,
        "kinds": kinds,
        "genres": _str_list(raw.get("genres"), "genres"),
        "decades": sorted({d for d in decades if d is not None and d % 10 == 0}),
        "studios": _str_list(raw.get("studios"), "studios"),
        "networks": _str_list(raw.get("networks"), "networks"),
        "collection": (collection or "").strip()[:120] or None,
        "min_score": round(float(min_score), 1) if min_score is not None else None,
        "kids": bool(raw.get("kids")),
        "added_days": _int(raw.get("added_days"), "added_days", 1, 3650),
        "show": show,
        "people": people,
        "order": order,
    }
    return spec


def _people(v: Any) -> List[Dict[str, Any]]:
    """a channel of someone's work: [{id, name}], up to five people"""
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > 5:
        raise LineupError("people must be a list of up to 5")
    out, seen = [], set()
    for p in v:
        if not isinstance(p, dict):
            raise LineupError("each person needs an id and a name")
        pid = _int(p.get("id"), "a person's id", 1, 2**31)
        name = " ".join(str(p.get("name") or "").split())[:80]
        if not pid or not name or pid in seen:
            continue
        seen.add(pid)
        out.append({"id": pid, "name": name})
    return out


def _channel_row(r) -> Dict[str, Any]:
    try:
        spec = json.loads(r[2])
    except (TypeError, ValueError):
        spec = {}
    return {"id": r[0], "scope": "server" if r[1] is None else "mine", "spec": spec,
            "created_at": r[3], "updated_at": r[4]}


def list_channels(db: Any, profile_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """the server's channels and this profile's own"""
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, owner_profile_id, spec_json, created_at, updated_at FROM {CHANNELS} "
            "WHERE owner_profile_id IS NULL OR owner_profile_id = ? ORDER BY created_at",
            (int(profile_id),)).fetchall()
    out = [_channel_row(r) for r in rows]
    return {"server": [c for c in out if c["scope"] == "server"],
            "mine": [c for c in out if c["scope"] == "mine"]}


def add_channel(db: Any, owner: Optional[int], spec: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """owner None: a server channel (the caller checked it's an admin)"""
    clean = clean_spec(spec)
    ensure_schema(db)
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {CHANNELS} WHERE owner_profile_id IS ?", (owner,)).fetchone()[0]
        if count >= MAX_CHANNELS:
            raise LineupError(f"that's {MAX_CHANNELS} channels: take one out first")
        cid = "c-" + uuid.uuid4().hex[:12]
        conn.execute(f"INSERT INTO {CHANNELS} (id, owner_profile_id, spec_json, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?)", (cid, owner, json.dumps(clean), t, t))
        conn.commit()
        row = conn.execute(f"SELECT id, owner_profile_id, spec_json, created_at, updated_at FROM {CHANNELS} "
                           "WHERE id = ?", (cid,)).fetchone()
    return _channel_row(row)


def _owned(conn, channel_id: str, profile_id: int, admin: bool):
    r = conn.execute(f"SELECT id, owner_profile_id, spec_json, created_at, updated_at FROM {CHANNELS} "
                     "WHERE id = ?", (str(channel_id),)).fetchone()
    if not r:
        return None
    if r[1] is None:
        return r if admin else None
    return r if int(r[1]) == int(profile_id) else None


def edit_channel(db: Any, profile_id: int, channel_id: str, spec: Any, *, admin: bool = False,
                 now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """change your own channel (an admin: a server one too). None: not yours"""
    clean = clean_spec(spec)
    ensure_schema(db)
    with db._get_connection() as conn:
        r = _owned(conn, channel_id, profile_id, admin)
        if not r:
            return None
        conn.execute(f"UPDATE {CHANNELS} SET spec_json = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(clean), time.time() if now is None else now, r[0]))
        conn.commit()
        r = conn.execute(f"SELECT id, owner_profile_id, spec_json, created_at, updated_at FROM {CHANNELS} "
                         "WHERE id = ?", (r[0],)).fetchone()
    return _channel_row(r)


def remove_channel(db: Any, profile_id: int, channel_id: str, *, admin: bool = False) -> bool:
    ensure_schema(db)
    with db._get_connection() as conn:
        r = _owned(conn, channel_id, profile_id, admin)
        if not r:
            return False
        conn.execute(f"DELETE FROM {CHANNELS} WHERE id = ?", (r[0],))
        # nobody's lineup keeps a place for a channel that's gone
        conn.execute(f"DELETE FROM {LINEUP} WHERE channel_id = ?", (r[0],))
        conn.commit()
        return True


# ── a profile's lineup ───────────────────────────────────────────────────

def get_lineup(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(f"SELECT channel_id, position, hidden, name FROM {LINEUP} "
                            "WHERE profile_id = ? ORDER BY position", (int(profile_id),)).fetchall()
    return [{"channel_id": r[0], "position": r[1], "hidden": bool(r[2]), "name": r[3]} for r in rows]


def put_lineup(db: Any, profile_id: int, entries: Any) -> List[Dict[str, Any]]:
    """the whole lineup, replaced: [{channel_id, hidden?, name?}] in order"""
    if not isinstance(entries, list) or len(entries) > MAX_LINEUP:
        raise LineupError(f"a lineup is a list of up to {MAX_LINEUP}")
    clean, seen = [], set()
    for e in entries:
        if not isinstance(e, dict):
            raise LineupError("each entry needs a channel_id")
        cid = str(e.get("channel_id") or "").strip()[:120]
        if not cid or cid in seen:
            continue
        seen.add(cid)
        name = " ".join(str(e.get("name") or "").split())[:NAME_MAX] or None
        clean.append((cid, bool(e.get("hidden")), name))
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {LINEUP} WHERE profile_id = ?", (int(profile_id),))
        conn.executemany(
            f"INSERT INTO {LINEUP} (profile_id, channel_id, position, hidden, name) VALUES (?, ?, ?, ?, ?)",
            [(int(profile_id), cid, i, int(h), n) for i, (cid, h, n) in enumerate(clean)])
        conn.commit()
    return get_lineup(db, profile_id)


def forget_profile(db: Any, profile_id: int) -> int:
    """a deleted profile's own channels (its lineup goes with player.db's sweep)"""
    ensure_schema(db)
    with db._get_connection() as conn:
        n = conn.execute(f"DELETE FROM {CHANNELS} WHERE owner_profile_id = ?", (int(profile_id),)).rowcount
        conn.commit()
        return n
