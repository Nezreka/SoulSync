"""player downloads, kept in player.db: each profile's devices and what is
on them, the adds and removes sent to a device from another screen
(wishes), smart rules, plays made offline, and the copies the server
prepared.

tables are made by the first write. reads of a table not made yet are
empty, never a create.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

DEVICES = "player_offline_devices"
ITEMS = "player_offline_items"
WISHES = "player_offline_wishes"
RULES = "player_offline_rules"
PLAYS = "player_offline_plays"
PREPARED = "player_offline_prepared"

STATES = ("downloaded", "downloading", "queued", "paused", "failed")
PLATFORMS = ("android", "web")
MAX_ITEMS = 5000

_ready: set = set()

_DDL = f"""
CREATE TABLE IF NOT EXISTS {DEVICES} (
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    free_bytes INTEGER,
    limit_bytes INTEGER,
    used_bytes INTEGER,
    wifi_only INTEGER NOT NULL DEFAULT 1,
    paired_id INTEGER,
    last_seen REAL NOT NULL,
    PRIMARY KEY (profile_id, device_id)
);
CREATE TABLE IF NOT EXISTS {ITEMS} (
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    quality TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    title TEXT,
    subtitle TEXT,
    artwork TEXT,
    duration_s INTEGER,
    group_json TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (profile_id, device_id, item_type, item_id)
);
CREATE TABLE IF NOT EXISTS {WISHES} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    quality TEXT NOT NULL,
    title TEXT,
    subtitle TEXT,
    artwork TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE (profile_id, device_id, type, target_id)
);
CREATE TABLE IF NOT EXISTS {RULES} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    quality TEXT NOT NULL,
    keep_next INTEGER NOT NULL DEFAULT 3,
    delete_watched INTEGER NOT NULL DEFAULT 0,
    follow INTEGER NOT NULL DEFAULT 1,
    title TEXT,
    artwork TEXT,
    members_json TEXT,
    created_at REAL NOT NULL,
    UNIQUE (profile_id, device_id, type, target_id)
);
CREATE TABLE IF NOT EXISTS {PLAYS} (
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (profile_id, device_id, item_type, item_id, at)
);
CREATE TABLE IF NOT EXISTS {PREPARED} (
    id TEXT PRIMARY KEY,
    file_key TEXT NOT NULL,
    quality TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT,
    filename TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    last_used REAL NOT NULL,
    UNIQUE (file_key, quality)
);
CREATE INDEX IF NOT EXISTS idx_{PREPARED}_used ON {PREPARED} (status, last_used);
"""


def _key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


def ensure_schema(db: Any) -> None:
    """made by the first write"""
    if _key(db) in _ready:
        return
    with db._get_connection() as conn:
        conn.executescript(_DDL)
        conn.commit()
    _ready.add(_key(db))


def _read(db: Any, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
    """a read that finds nothing when the tables aren't made yet"""
    try:
        with db._get_connection() as conn:
            return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise


def _clip(value: Any, n: int) -> str:
    return " ".join(str(value or "").split())[:n]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


# ── who may ──────────────────────────────────────────────────────────────

def downloads_allowed(db: Any, profile_id: int) -> bool:
    """the admin's per-profile switch (core/player/downloads.py), read
    without making its table"""
    rows = _read(db, "SELECT allow_downloads FROM player_download_permissions WHERE profile_id = ?",
                 (int(profile_id),))
    return bool(rows[0][0]) if rows else True


# ── devices ──────────────────────────────────────────────────────────────

class ReportError(ValueError):
    pass


class NotYourDevice(PermissionError):
    pass


def clean_device_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 80 or not all(c.isalnum() or c in "-_.:" for c in text):
        raise ReportError("device_id is a short id of letters, digits and - _ . :")
    return text


def device(db: Any, profile_id: int, device_id: str) -> Optional[Dict[str, Any]]:
    rows = _read(db, f"SELECT * FROM {DEVICES} WHERE profile_id = ? AND device_id = ?",
                 (int(profile_id), device_id))
    return dict(rows[0]) if rows else None


def report(db: Any, profile_id: int, body: Dict[str, Any], *, paired_id: Optional[int] = None,
           describe=None) -> Dict[str, Any]:
    """a device's whole truth replaces what was kept for it. ``describe``
    fills titles for items new to the server ({"type:id": meta})."""
    if not isinstance(body, dict):
        raise ReportError("expected an object")
    dev_id = clean_device_id(body.get("device_id"))
    platform = str(body.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        raise ReportError("platform is android or web")
    raw_items = body.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list) or len(raw_items) > MAX_ITEMS:
        raise ReportError(f"items is a list of at most {MAX_ITEMS}")
    items: Dict[tuple, Dict[str, Any]] = {}
    for it in raw_items:
        if not isinstance(it, dict):
            raise ReportError("each item is an object")
        itype = str(it.get("item_type") or "").strip().lower()
        iid = str(it.get("item_id") or "").strip()
        if not itype or not iid or len(iid) > 400 or len(itype) > 32:
            raise ReportError("each item needs item_type and item_id")
        state = str(it.get("state") or "downloaded").strip().lower()
        if state not in STATES:
            raise ReportError(f"state is one of {', '.join(STATES)}")
        try:
            progress = float(it.get("progress") or 0)
        except (TypeError, ValueError):
            progress = 0.0
        items[(itype, iid)] = {
            "quality": _clip(it.get("quality") or "original", 16) or "original",
            "size_bytes": _int_or_none(it.get("size_bytes")) or 0,
            "state": state, "progress": max(0.0, min(1.0, progress)),
        }
    now = time.time()
    ensure_schema(db)
    pid = int(profile_id)
    with db._get_connection() as conn:
        old_dev = conn.execute(f"SELECT paired_id FROM {DEVICES} WHERE profile_id = ? AND device_id = ?",
                               (pid, dev_id)).fetchone()
        if old_dev is not None and old_dev[0] is not None and paired_id is not None \
                and int(old_dev[0]) != int(paired_id):
            raise NotYourDevice("that device id belongs to another paired device")
        bound = paired_id if paired_id is not None else (old_dev[0] if old_dev is not None else None)
        old = {(r["item_type"], r["item_id"]): dict(r) for r in conn.execute(
            f"SELECT * FROM {ITEMS} WHERE profile_id = ? AND device_id = ?", (pid, dev_id))}
    fresh = [k for k in items if k not in old or not old[k].get("title")]
    meta = describe(fresh) if (describe and fresh) else {}
    with db._get_connection() as conn:
        conn.execute(
            f"""INSERT INTO {DEVICES} (profile_id, device_id, device_name, platform, free_bytes,
                    limit_bytes, used_bytes, wifi_only, paired_id, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, device_id) DO UPDATE SET
                    device_name = excluded.device_name, platform = excluded.platform,
                    free_bytes = excluded.free_bytes, limit_bytes = excluded.limit_bytes,
                    used_bytes = excluded.used_bytes, wifi_only = excluded.wifi_only,
                    paired_id = excluded.paired_id, last_seen = excluded.last_seen""",
            (pid, dev_id, _clip(body.get("device_name"), 120) or "Device", platform,
             _int_or_none(body.get("free_bytes")), _int_or_none(body.get("limit_bytes")),
             _int_or_none(body.get("used_bytes")), 1 if body.get("wifi_only", True) else 0, bound, now))
        conn.execute(f"DELETE FROM {ITEMS} WHERE profile_id = ? AND device_id = ?", (pid, dev_id))
        for (itype, iid), it in items.items():
            prev = old.get((itype, iid)) or {}
            m = meta.get(f"{itype}:{iid}") or {}
            group = m.get("group")
            conn.execute(
                f"""INSERT INTO {ITEMS} (profile_id, device_id, item_type, item_id, quality, size_bytes,
                        state, progress, title, subtitle, artwork, duration_s, group_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, dev_id, itype, iid, it["quality"], it["size_bytes"], it["state"], it["progress"],
                 m.get("title") or prev.get("title"), m.get("subtitle") or prev.get("subtitle"),
                 m.get("artwork") or prev.get("artwork"), m.get("duration_s") or prev.get("duration_s"),
                 json.dumps(group) if group else prev.get("group_json"), now))
        conn.commit()
    added = [k for k in items if items[k]["state"] == "downloaded"
             and (k not in old or old[k]["state"] != "downloaded")]
    gone = [k for k in old if old[k]["state"] == "downloaded"
            and (k not in items or items[k]["state"] != "downloaded")]
    _settle_removes(db, pid, dev_id, set(items))
    return {"device_id": dev_id, "added": added, "gone": gone, "items": items}


def _settle_removes(db: Any, pid: int, dev_id: str, held: set) -> None:
    """a remove wish is done once none of its items are left. a single
    item's wish is checked here; a group's is checked by wanted()."""
    with db._get_connection() as conn:
        for r in conn.execute(f"SELECT id, type, target_id FROM {WISHES} WHERE profile_id = ? "
                              "AND device_id = ? AND action = 'remove'", (pid, dev_id)).fetchall():
            from core.player.offline_catalog import ITEM_KINDS
            if r["type"] in ITEM_KINDS and (r["type"], r["target_id"]) not in held:
                conn.execute(f"DELETE FROM {WISHES} WHERE id = ?", (r["id"],))
        conn.commit()


def devices(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in _read(
        db, f"SELECT * FROM {DEVICES} WHERE profile_id = ? ORDER BY last_seen DESC", (int(profile_id),))]


def items(db: Any, profile_id: int, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if device_id is None:
        rows = _read(db, f"SELECT * FROM {ITEMS} WHERE profile_id = ?", (int(profile_id),))
    else:
        rows = _read(db, f"SELECT * FROM {ITEMS} WHERE profile_id = ? AND device_id = ?",
                     (int(profile_id), device_id))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["group"] = json.loads(d.pop("group_json") or "null")
        except (TypeError, ValueError):
            d["group"] = None
        out.append(d)
    return out


# ── wishes ───────────────────────────────────────────────────────────────

def wishes(db: Any, profile_id: int, device_id: str, *, pending: bool = False) -> List[Dict[str, Any]]:
    """a device's wishes. an add the device has done is kept (``done``): it
    keeps what it brought from being dropped by a playlist that moved on"""
    rows = _read(db, f"SELECT * FROM {WISHES} WHERE profile_id = ? AND device_id = ?"
                     f"{' AND done = 0' if pending else ''} ORDER BY id", (int(profile_id), device_id))
    return [dict(r) for r in rows]


def wish(db: Any, profile_id: int, device_id: str, action: str, wtype: str, target: str,
         quality: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """an add replaces an earlier remove of the same thing and the other
    way round; a remove of something only wished for just drops the wish"""
    ensure_schema(db)
    meta = meta or {}
    pid = int(profile_id)
    with db._get_connection() as conn:
        old = conn.execute(f"SELECT action, done FROM {WISHES} WHERE profile_id = ? AND device_id = ? "
                           "AND type = ? AND target_id = ?", (pid, device_id, wtype, target)).fetchone()
        held = conn.execute(f"SELECT 1 FROM {ITEMS} WHERE profile_id = ? AND device_id = ? "
                            "AND item_type = ? AND item_id = ?", (pid, device_id, wtype, target)).fetchone()
        if action == "remove" and old is not None and old[0] == "add" and not old[1] and held is None \
                and _single(wtype):
            conn.execute(f"DELETE FROM {WISHES} WHERE profile_id = ? AND device_id = ? AND type = ? "
                         "AND target_id = ?", (pid, device_id, wtype, target))
        else:
            conn.execute(
                f"""INSERT INTO {WISHES} (profile_id, device_id, action, type, target_id, quality, title,
                        subtitle, artwork, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id, device_id, type, target_id) DO UPDATE SET
                        action = excluded.action, quality = excluded.quality, title = excluded.title,
                        subtitle = excluded.subtitle, artwork = excluded.artwork, done = 0,
                        created_at = excluded.created_at""",
                (pid, device_id, action, wtype, target, quality, meta.get("title"), meta.get("subtitle"),
                 meta.get("artwork"), time.time()))
        if action == "remove":
            # removing a group takes its smart rule with it
            conn.execute(f"DELETE FROM {RULES} WHERE profile_id = ? AND device_id = ? AND type = ? "
                         "AND target_id = ?", (pid, device_id, wtype, target))
        conn.commit()


def _single(wtype: str) -> bool:
    from core.player.offline_catalog import ITEM_KINDS
    return wtype in ITEM_KINDS


def wish_done(db: Any, wish_id: int) -> None:
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {WISHES} SET done = 1 WHERE id = ?", (int(wish_id),))
        conn.commit()


def drop_wish(db: Any, wish_id: int) -> None:
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {WISHES} WHERE id = ?", (int(wish_id),))
        conn.commit()


# ── rules ────────────────────────────────────────────────────────────────

def rule_public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"rule_id": int(r["id"]), "type": r["type"], "id": r["target_id"], "quality": r["quality"],
            "keep_next": int(r["keep_next"]), "delete_watched": bool(r["delete_watched"]),
            "follow": bool(r["follow"]), "title": r.get("title"), "artwork": r.get("artwork"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["created_at"]))}


def rules(db: Any, profile_id: int, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if device_id is None:
        rows = _read(db, f"SELECT * FROM {RULES} WHERE profile_id = ? ORDER BY id", (int(profile_id),))
    else:
        rows = _read(db, f"SELECT * FROM {RULES} WHERE profile_id = ? AND device_id = ? ORDER BY id",
                     (int(profile_id), device_id))
    return [dict(r) for r in rows]


def put_rule(db: Any, profile_id: int, device_id: str, rtype: str, target: str, quality: str,
             keep_next: int, delete_watched: bool, follow: bool,
             meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_schema(db)
    meta = meta or {}
    pid = int(profile_id)
    with db._get_connection() as conn:
        conn.execute(
            f"""INSERT INTO {RULES} (profile_id, device_id, type, target_id, quality, keep_next,
                    delete_watched, follow, title, artwork, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, device_id, type, target_id) DO UPDATE SET
                    quality = excluded.quality, keep_next = excluded.keep_next,
                    delete_watched = excluded.delete_watched, follow = excluded.follow,
                    title = excluded.title, artwork = excluded.artwork""",
            (pid, device_id, rtype, target, quality, int(keep_next), 1 if delete_watched else 0,
             1 if follow else 0, meta.get("title"), meta.get("artwork"), time.time()))
        # a rule for it replaces a pending remove of it
        conn.execute(f"DELETE FROM {WISHES} WHERE profile_id = ? AND device_id = ? AND type = ? "
                     "AND target_id = ? AND action = 'remove'", (pid, device_id, rtype, target))
        conn.commit()
        row = conn.execute(f"SELECT * FROM {RULES} WHERE profile_id = ? AND device_id = ? AND type = ? "
                           "AND target_id = ?", (pid, device_id, rtype, target)).fetchone()
    return dict(row)


def delete_rule(db: Any, profile_id: int, rule_id: int) -> bool:
    if not _read(db, f"SELECT 1 FROM {RULES} WHERE id = ? AND profile_id = ?", (int(rule_id), int(profile_id))):
        return False
    with db._get_connection() as conn:
        cur = conn.execute(f"DELETE FROM {RULES} WHERE id = ? AND profile_id = ?", (int(rule_id), int(profile_id)))
        conn.commit()
        return bool(cur.rowcount)


def set_members(db: Any, rule_id: int, members: List[str]) -> None:
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {RULES} SET members_json = ? WHERE id = ?", (json.dumps(members), int(rule_id)))
        conn.commit()


# ── plays made offline ───────────────────────────────────────────────────

def play_once(db: Any, profile_id: int, device_id: str, item_type: str, item_id: str, at: str) -> bool:
    """True the first time this device reports this play"""
    ensure_schema(db)
    with db._get_connection() as conn:
        cur = conn.execute(f"INSERT OR IGNORE INTO {PLAYS} (profile_id, device_id, item_type, item_id, at) "
                           "VALUES (?, ?, ?, ?, ?)", (int(profile_id), device_id, item_type, item_id, at))
        conn.commit()
        return bool(cur.rowcount)


# ── prepared copies ──────────────────────────────────────────────────────

def prepared(db: Any, prep_id: str) -> Optional[Dict[str, Any]]:
    rows = _read(db, f"SELECT * FROM {PREPARED} WHERE id = ?", (str(prep_id),))
    return dict(rows[0]) if rows else None


def prepared_for(db: Any, file_key: str, quality: str) -> Optional[Dict[str, Any]]:
    rows = _read(db, f"SELECT * FROM {PREPARED} WHERE file_key = ? AND quality = ?", (file_key, quality))
    return dict(rows[0]) if rows else None


def put_prepared(db: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema(db)
    now = time.time()
    with db._get_connection() as conn:
        conn.execute(
            f"""INSERT INTO {PREPARED} (id, file_key, quality, item_type, item_id, status, progress,
                    size_bytes, path, filename, error, created_at, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status, progress = excluded.progress,
                    size_bytes = excluded.size_bytes, path = excluded.path, filename = excluded.filename,
                    error = excluded.error, last_used = excluded.last_used""",
            (row["id"], row["file_key"], row["quality"], row["item_type"], row["item_id"], row["status"],
             float(row.get("progress") or 0), int(row.get("size_bytes") or 0), row.get("path"),
             row.get("filename"), row.get("error"), now, now))
        conn.commit()
    return prepared(db, row["id"]) or row


def update_prepared(db: Any, prep_id: str, **fields: Any) -> None:
    allowed = {"status", "progress", "size_bytes", "path", "filename", "error", "last_used"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {PREPARED} SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?",  # noqa: S608
                     (*sets.values(), str(prep_id)))
        conn.commit()


def delete_prepared(db: Any, prep_id: str) -> None:
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {PREPARED} WHERE id = ?", (str(prep_id),))
        conn.commit()


def cached_copies(db: Any) -> List[Dict[str, Any]]:
    """converted copies on disk, oldest used first"""
    return [dict(r) for r in _read(
        db, f"SELECT * FROM {PREPARED} WHERE status = 'ready' AND path IS NOT NULL ORDER BY last_used")]


def unfinished(db: Any) -> List[Dict[str, Any]]:
    return [dict(r) for r in _read(
        db, f"SELECT * FROM {PREPARED} WHERE status IN ('queued', 'converting')")]


# ── the admin's settings (kept beside player setup, in player_setup) ─────

SETTING_DEFAULTS = {"allow_conversion_for_members": True, "max_conversions": 1, "cache_gb": 50}
SETTING_LIMITS = {"max_conversions": (1, 8), "cache_gb": (1, 2000)}
SETUP_TABLE = "player_setup"


def clean_setting(name: str, value: Any) -> Any:
    if name == "allow_conversion_for_members":
        if not isinstance(value, bool):
            raise ValueError("allow_conversion_for_members is true or false")
        return value
    if name in SETTING_LIMITS:
        lo, hi = SETTING_LIMITS[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= int(value) <= hi:
            raise ValueError(f"{name} is a number from {lo} to {hi}")
        return int(value)
    raise ValueError(f"no setting called {name}")


def get_settings(db: Any) -> Dict[str, Any]:
    out = dict(SETTING_DEFAULTS)
    for k, raw in _read(db, f"SELECT key, value_json FROM {SETUP_TABLE} WHERE key LIKE 'offline.%'"):
        name = k[len("offline."):]
        try:
            out[name] = clean_setting(name, json.loads(raw))
        except (ValueError, TypeError):
            continue
    return out


def put_settings(db: Any, changes: Dict[str, Any]) -> Dict[str, Any]:
    """all saved or none (raises ValueError)"""
    clean = {k: clean_setting(k, v) for k, v in changes.items()}
    with db._get_connection() as conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {SETUP_TABLE} (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        for k, v in clean.items():
            conn.execute(f"INSERT INTO {SETUP_TABLE} (key, value_json) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                         (f"offline.{k}", json.dumps(v)))
        conn.commit()
    return get_settings(db)
