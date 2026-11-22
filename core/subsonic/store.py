"""what the music app server keeps of its own: each music app's app
password and each device's saved play queue.

app passwords: classic music apps sign in with a username and a salted md5
of the password, so the server needs the password itself. it is kept
encrypted (core.secret_store, SoulSync's own key), shown once when the app
is set up, and goes with the device (revoked device: refused; the row is
deleted with it).

play queues: one per profile and device, so a phone and a car never
overwrite each other. this is the music apps' own queue, separate from
the player's resume store.

both tables carry profile_id, so deleting a profile clears them with every
other per-profile row (MusicDatabase.delete_profile's sweep).
"""

from __future__ import annotations

import json
import secrets
import string
from typing import Any, Callable, Dict, List, Optional

APP_PASSWORD_LEN = 20
_ALPHABET = string.ascii_letters + string.digits
QUEUE_MAX = 1000


def ensure_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subsonic_app_passwords (
            device_id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL,
            password_enc TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subsonic_app_passwords_profile "
                   "ON subsonic_app_passwords (profile_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subsonic_play_queues (
            profile_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,
            track_ids TEXT NOT NULL,
            current_id TEXT,
            position_ms INTEGER NOT NULL DEFAULT 0,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            changed_by TEXT,
            PRIMARY KEY (profile_id, device_id)
        )
    """)
    # stars on albums and artists (a starred song is a Liked Song) and
    # ratings on anything, per profile: SoulSync has neither of its own
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subsonic_stars (
            profile_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            starred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, kind, item_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subsonic_ratings (
            profile_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            rated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, kind, item_id)
        )
    """)


def new_app_password() -> str:
    """letters and digits only: some apps mangle anything else."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(APP_PASSWORD_LEN))


def _conn(db):
    return db._get_connection()


# ── app passwords ────────────────────────────────────────────────────────

def save_app_password(db, device_id: int, profile_id: int, password: str,
                      encrypt: Callable[[str], str]) -> None:
    with _conn(db) as conn:
        conn.execute("INSERT OR REPLACE INTO subsonic_app_passwords (device_id, profile_id, password_enc) "
                     "VALUES (?, ?, ?)", (int(device_id), int(profile_id), encrypt(password)))
        conn.commit()


def drop_app_password(db, device_id: int) -> None:
    with _conn(db) as conn:
        conn.execute("DELETE FROM subsonic_app_passwords WHERE device_id = ?", (int(device_id),))
        conn.commit()


def devices_with_passwords(db, profile_ids: List[int]) -> List[Dict[str, Any]]:
    """the live (not revoked) devices of these profiles that have an app
    password, each row with its ``password_enc``."""
    if not profile_ids:
        return []
    marks = ",".join("?" * len(profile_ids))
    with _conn(db) as conn:
        rows = conn.execute(
            "SELECT d.*, s.password_enc FROM subsonic_app_passwords s "
            "JOIN device_tokens d ON d.id = s.device_id "
            f"WHERE s.profile_id IN ({marks}) AND d.revoked_at IS NULL AND d.profile_id = s.profile_id",  # noqa: S608 - marks only
            [int(p) for p in profile_ids]).fetchall()
    return [dict(r) for r in rows]


def device_ids_with_passwords(db) -> List[int]:
    with _conn(db) as conn:
        return [int(r[0]) for r in conn.execute("SELECT device_id FROM subsonic_app_passwords").fetchall()]


# ── play queues ──────────────────────────────────────────────────────────

def save_queue(db, profile_id: int, device_id: int, track_ids: List[str], current: Optional[str],
               position_ms: int, changed_by: str = "") -> None:
    ids = [str(t) for t in track_ids][:QUEUE_MAX]
    with _conn(db) as conn:
        if not ids:
            conn.execute("DELETE FROM subsonic_play_queues WHERE profile_id = ? AND device_id = ?",
                         (int(profile_id), int(device_id)))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO subsonic_play_queues "
                "(profile_id, device_id, track_ids, current_id, position_ms, changed_at, changed_by) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                (int(profile_id), int(device_id), json.dumps(ids), current, max(0, int(position_ms or 0)),
                 (changed_by or "")[:80]))
        conn.commit()


def load_queue(db, profile_id: int, device_id: int) -> Optional[Dict[str, Any]]:
    """this device's queue, or the profile's newest one from another device
    when this one has none yet (pick up on the car where the phone left
    off)."""
    with _conn(db) as conn:
        row = conn.execute("SELECT * FROM subsonic_play_queues WHERE profile_id = ? AND device_id = ?",
                           (int(profile_id), int(device_id))).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM subsonic_play_queues WHERE profile_id = ? "
                               "ORDER BY changed_at DESC LIMIT 1", (int(profile_id),)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["track_ids"] = [str(t) for t in json.loads(out.get("track_ids") or "[]")]
    except (TypeError, ValueError):
        out["track_ids"] = []
    return out


# ── stars (albums, artists) and ratings ──────────────────────────────────

def star(db, profile_id: int, kind: str, item_id: int) -> None:
    with _conn(db) as conn:
        conn.execute("INSERT OR IGNORE INTO subsonic_stars (profile_id, kind, item_id) VALUES (?, ?, ?)",
                     (int(profile_id), kind, int(item_id)))
        conn.commit()


def unstar(db, profile_id: int, kind: str, item_id: int) -> None:
    with _conn(db) as conn:
        conn.execute("DELETE FROM subsonic_stars WHERE profile_id = ? AND kind = ? AND item_id = ?",
                     (int(profile_id), kind, int(item_id)))
        conn.commit()


def stars(db, profile_id: int) -> List[Dict[str, Any]]:
    """newest first."""
    with _conn(db) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT kind, item_id, starred_at FROM subsonic_stars WHERE profile_id = ? "
            "ORDER BY starred_at DESC, rowid DESC", (int(profile_id),)).fetchall()]


def set_rating(db, profile_id: int, kind: str, item_id: int, rating: int) -> None:
    """1-5 sets it, 0 clears it."""
    with _conn(db) as conn:
        if int(rating) <= 0:
            conn.execute("DELETE FROM subsonic_ratings WHERE profile_id = ? AND kind = ? AND item_id = ?",
                         (int(profile_id), kind, int(item_id)))
        else:
            conn.execute("INSERT OR REPLACE INTO subsonic_ratings (profile_id, kind, item_id, rating) "
                         "VALUES (?, ?, ?, ?)", (int(profile_id), kind, int(item_id), min(5, int(rating))))
        conn.commit()


def ratings(db, profile_id: int) -> List[Dict[str, Any]]:
    """highest first."""
    with _conn(db) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT kind, item_id, rating FROM subsonic_ratings WHERE profile_id = ? "
            "ORDER BY rating DESC, rated_at DESC", (int(profile_id),)).fetchall()]


__all__ = ["star", "unstar", "stars", "set_rating", "ratings", "ensure_tables", "new_app_password", "save_app_password", "drop_app_password",
           "devices_with_passwords", "device_ids_with_passwords", "save_queue", "load_queue",
           "APP_PASSWORD_LEN", "QUEUE_MAX"]
