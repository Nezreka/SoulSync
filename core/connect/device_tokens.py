"""paired devices and their tokens (PAIRING_SPEC.md 4).

a device gets one token, shown to it once; only its SHA-256 is stored. the
token starts unprofiled: it may only fetch the pick list and complete the
pick, and it dies if no profile is picked within PICK_WINDOW seconds. after
the pick it acts as that profile with playback scope only, on the routes
that opt in (core/connect/device_access.py).

the table is ``device_tokens`` in the music database. its ``profile_id``
column means deleting a profile deletes its devices with every other
per-profile row (MusicDatabase.delete_profile's sweep).

``sk_`` API keys are a different thing and never touch this table.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

TOKEN_PREFIX = "sdt_"
PICK_WINDOW = 15 * 60
SCOPE_PLAYBACK = "playback"
# what kind of app a device is. a music app only sees profiles with the
# "Use music apps" permission. new kinds are added on purpose, with their
# own permission rule (core/connect/pick.py)
KIND_MUSIC = "music"
KIND_PLAYER = "player"
KINDS = (KIND_MUSIC, KIND_PLAYER)
# last_seen is written at most this often per device
TOUCH_EVERY = 60.0

_touched: Dict[int, float] = {}
_touch_lock = threading.Lock()


def ensure_tables(cursor) -> None:
    """the table, idempotent. called from the music database's migrations."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            name TEXT,
            platform TEXT,
            app TEXT,
            kind TEXT NOT NULL DEFAULT 'music',
            profile_id INTEGER,
            scope TEXT NOT NULL DEFAULT 'playback',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP,
            last_ip TEXT,
            revoked_at TIMESTAMP,
            pick_deadline REAL,
            invite_code_id INTEGER
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_tokens_profile ON device_tokens (profile_id)")


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def looks_like_token(value: str) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX) and len(value) <= 128


def _clip(value: Any, n: int) -> str:
    return str(value or "").strip()[:n]


# ── the state of one row ─────────────────────────────────────────────────

LIVE = "live"            # profiled, usable
UNPROFILED = "unprofiled"  # inside its pick window
EXPIRED = "expired"      # the pick window passed with no pick
REVOKED = "revoked"


def state_of(row: Optional[Dict[str, Any]], now: float) -> Optional[str]:
    """None for no row."""
    if not row:
        return None
    if row.get("revoked_at"):
        return REVOKED
    if row.get("profile_id") is None:
        deadline = row.get("pick_deadline")
        if deadline is None or float(deadline) <= now:
            return EXPIRED
        return UNPROFILED
    return LIVE


# ── the store ────────────────────────────────────────────────────────────

def _conn(db):
    return db._get_connection()


def create(db, *, name: str, platform: str = "", app: str = "", kind: str = KIND_MUSIC,
           ip: str = "", invite_code_id: Optional[int] = None,
           now: Optional[float] = None) -> Tuple[str, int]:
    """(raw token, row id) for a new unprofiled device."""
    if kind not in KINDS:
        raise ValueError("unknown device kind %r" % kind)
    now = time.time() if now is None else now
    token = new_token()
    with _conn(db) as conn:
        cur = conn.execute(
            "INSERT INTO device_tokens (token_hash, name, platform, app, kind, scope, last_ip, "
            "pick_deadline, invite_code_id, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (hash_token(token), _clip(name, 80) or "Device", _clip(platform, 40), _clip(app, 80),
             kind, SCOPE_PLAYBACK, _clip(ip, 64), now + PICK_WINDOW, invite_code_id))
        conn.commit()
        return token, int(cur.lastrowid)


def find(db, token: str) -> Optional[Dict[str, Any]]:
    if not looks_like_token(token):
        return None
    with _conn(db) as conn:
        row = conn.execute("SELECT * FROM device_tokens WHERE token_hash = ?",
                           (hash_token(token),)).fetchone()
        return dict(row) if row else None


def get(db, device_id: int) -> Optional[Dict[str, Any]]:
    with _conn(db) as conn:
        row = conn.execute("SELECT * FROM device_tokens WHERE id = ?", (int(device_id),)).fetchone()
        return dict(row) if row else None


def pick_profile(db, device_id: int, profile_id: int, *, now: Optional[float] = None) -> bool:
    """bind the device to a profile. only once, only while unprofiled, not
    revoked and inside its pick window: one UPDATE, so two picks can't race."""
    now = time.time() if now is None else now
    with _conn(db) as conn:
        cur = conn.execute(
            "UPDATE device_tokens SET profile_id = ?, pick_deadline = NULL "
            "WHERE id = ? AND profile_id IS NULL AND revoked_at IS NULL AND pick_deadline > ?",
            (int(profile_id), int(device_id), now))
        conn.commit()
        return cur.rowcount > 0


def switch_profile(db, device_id: int, profile_id: int) -> bool:
    """Switch a live (already-paired, non-revoked) device to another household profile."""
    with _conn(db) as conn:
        cur = conn.execute(
            "UPDATE device_tokens SET profile_id = ?, pick_deadline = NULL "
            "WHERE id = ? AND profile_id IS NOT NULL AND revoked_at IS NULL",
            (int(profile_id), int(device_id)))
        conn.commit()
        return cur.rowcount > 0


def revoke(db, device_id: int) -> bool:
    with _conn(db) as conn:
        cur = conn.execute("UPDATE device_tokens SET revoked_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND revoked_at IS NULL", (int(device_id),))
        conn.commit()
        return cur.rowcount > 0


def revoke_for_profile(db, profile_id: int) -> int:
    with _conn(db) as conn:
        cur = conn.execute("UPDATE device_tokens SET revoked_at = CURRENT_TIMESTAMP "
                           "WHERE profile_id = ? AND revoked_at IS NULL", (int(profile_id),))
        conn.commit()
        return cur.rowcount or 0


def rename(db, device_id: int, name: str) -> bool:
    name = _clip(name, 80)
    if not name:
        return False
    with _conn(db) as conn:
        cur = conn.execute("UPDATE device_tokens SET name = ? WHERE id = ? AND revoked_at IS NULL",
                           (name, int(device_id)))
        conn.commit()
        return cur.rowcount > 0


def list_devices(db, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """every device that can still be used or is waiting for its pick,
    newest first. revoked and expired ones are left off; the hash never
    leaves this module."""
    now = time.time() if now is None else now
    with _conn(db) as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.platform, d.app, d.kind, d.profile_id, d.scope, d.created_at, "
            "d.last_seen_at, d.last_ip, d.pick_deadline, p.name AS profile_name "
            "FROM device_tokens d LEFT JOIN profiles p ON p.id = d.profile_id "
            "WHERE d.revoked_at IS NULL AND (d.profile_id IS NOT NULL OR d.pick_deadline > ?) "
            "ORDER BY d.id DESC", (now,)).fetchall()
    return [dict(r) for r in rows]


def sweep_expired(db, *, now: Optional[float] = None) -> int:
    """unpicked devices whose window passed, and their rows: gone."""
    now = time.time() if now is None else now
    with _conn(db) as conn:
        cur = conn.execute("DELETE FROM device_tokens WHERE profile_id IS NULL AND pick_deadline <= ?",
                           (now,))
        conn.commit()
        return cur.rowcount or 0


def touch(db, device_id: int, ip: str, *, now: Optional[float] = None) -> None:
    """last seen and where, at most every TOUCH_EVERY seconds per device."""
    mono = time.monotonic() if now is None else now
    with _touch_lock:
        if mono - _touched.get(int(device_id), -TOUCH_EVERY * 2) < TOUCH_EVERY:
            return
        _touched[int(device_id)] = mono
    try:
        with _conn(db) as conn:
            conn.execute("UPDATE device_tokens SET last_seen_at = CURRENT_TIMESTAMP, last_ip = ? WHERE id = ?",
                         (_clip(ip, 64), int(device_id)))
            conn.commit()
    except Exception:  # noqa: BLE001, S110 - last seen is a nicety, the request isn't
        pass


def public_row(row: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """a device as the admin's list shows it."""
    now = time.time() if now is None else now
    out = {k: row.get(k) for k in ("id", "name", "platform", "app", "kind", "profile_id",
                                     "profile_name", "scope", "created_at", "last_seen_at", "last_ip")}
    out["state"] = state_of(row, now)
    deadline = row.get("pick_deadline")
    out["pick_seconds_left"] = max(0, int(float(deadline) - now)) if deadline and out["state"] == UNPROFILED else None
    return out


def _reset_for_tests() -> None:
    with _touch_lock:
        _touched.clear()


__all__ = ["TOKEN_PREFIX", "PICK_WINDOW", "SCOPE_PLAYBACK", "KIND_MUSIC", "KINDS", "ensure_tables",
           "hash_token", "new_token", "looks_like_token", "state_of", "LIVE", "UNPROFILED", "EXPIRED",
           "REVOKED", "create", "find", "get", "pick_profile", "revoke", "revoke_for_profile", "rename",
           "list_devices", "sweep_expired", "touch", "public_row"]
