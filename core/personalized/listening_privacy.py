"""who may see your listening, and a private session that keeps plays out.

share listening: off for everyone until they turn it on, existing profiles
included. a blend ("You + Them") only exists when both piles share, and a
pile shares only when every profile that plays into it said yes: the shared
pile (owner 1) holds the plays of every profile without an account of its
own, so one yes can't speak for the others (core.listening_scope).

the For You shelf asks once ("asked" remembers the answer, yes or no).

private session: while it's on, the web player's plays aren't recorded at all,
so they never reach taste, blends, mixes, stats or a scrobble. it turns itself
off after PRIVATE_SESSION_HOURS so a forgotten switch doesn't eat a month of
history.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("personalized.listening_privacy")

PRIVATE_SESSION_HOURS = 6

COLUMNS = (
    ("share_listening", "INTEGER DEFAULT 0"),
    ("share_listening_asked", "INTEGER DEFAULT 0"),
    ("private_session_until", "TEXT DEFAULT NULL"),
)


def ensure_schema(cursor) -> None:
    """add the columns; every existing profile starts not sharing"""
    try:
        cols = {r[1] for r in cursor.execute("PRAGMA table_info(profiles)").fetchall()}
    except sqlite3.Error:
        return
    if not cols:
        return
    for col, decl in COLUMNS:
        if col not in cols:
            try:
                cursor.execute(f"ALTER TABLE profiles ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # added by a parallel boot


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pid(profile_id: Any) -> Optional[int]:
    try:
        return int(profile_id)
    except (TypeError, ValueError):
        return None


def _cols(conn) -> Set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}


def get_privacy(database, profile_id: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{share_listening, asked, private_session, private_until} for one profile"""
    out = {"share_listening": False, "asked": False, "private_session": False, "private_until": None}
    pid = _pid(profile_id)
    if pid is None:
        return out
    conn = database._get_connection()
    try:
        if "share_listening" not in _cols(conn):
            return out
        row = conn.execute(
            "SELECT share_listening, share_listening_asked, private_session_until "
            "FROM profiles WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return out
    until = row[2] or None
    live = bool(until and until > _stamp(now or _now()))
    out.update(share_listening=bool(row[0]), asked=bool(row[1]),
               private_session=live, private_until=until if live else None)
    return out


def _update(database, profile_id: Any, sql: str, args: List[Any]) -> bool:
    pid = _pid(profile_id)
    if pid is None:
        return False
    conn = database._get_connection()
    try:
        ensure_schema(conn.cursor())
        cur = conn.execute(f"UPDATE profiles SET {sql} WHERE id = ?", [*args, pid])
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_share_listening(database, profile_id: Any, on: bool) -> bool:
    """turning it on or off also counts as having answered the ask"""
    return _update(database, profile_id,
                   "share_listening = ?, share_listening_asked = 1", [1 if on else 0])


def mark_asked(database, profile_id: Any) -> bool:
    """"not now": stop asking, change nothing else"""
    return _update(database, profile_id, "share_listening_asked = 1", [])


def set_private_session(database, profile_id: Any, on: bool,
                        now: Optional[datetime] = None) -> Optional[str]:
    """start or end a private session. returns when it ends, None when off"""
    until = _stamp((now or _now()) + timedelta(hours=PRIVATE_SESSION_HOURS)) if on else None
    _update(database, profile_id, "private_session_until = ?", [until])
    return until


def in_private_session(database, profile_id: Any, now: Optional[datetime] = None) -> bool:
    try:
        return bool(get_privacy(database, profile_id, now)["private_session"])
    except Exception as e:  # noqa: BLE001 - can't tell: record, as before this existed
        logger.debug("private session lookup failed for %s: %s", profile_id, e)
        return False


def sharing_piles(database) -> Set[int]:
    """the listening piles another profile may see: every profile that plays
    into the pile (turned-off ones aside) has share listening on"""
    from core.listening_scope import SHARED_OWNER, _owns_pile_sql
    conn = database._get_connection()
    try:
        cols = _cols(conn)
        if "share_listening" not in cols:
            return set()
        disabled = "COALESCE(disabled, 0)" if "disabled" in cols else "0"
        rows = conn.execute(
            f"SELECT id, COALESCE(share_listening, 0), {disabled}, "
            f"CASE WHEN ({_owns_pile_sql(conn)}) THEN 1 ELSE 0 END FROM profiles").fetchall()
    finally:
        conn.close()
    verdict: Dict[int, bool] = {}
    for pid, share, off, owns in rows:
        pid = int(pid)
        pile = pid if (owns or pid == SHARED_OWNER) else SHARED_OWNER
        if off and pid != SHARED_OWNER:
            continue
        verdict[pile] = verdict.get(pile, True) and bool(share)
    return {pile for pile, ok in verdict.items() if ok}


def global_shelf_piles(database) -> List[int]:
    """the piles an install-wide shelf (seasonal rewind) may read: the shared
    pile, as it always did, plus own piles whose people share listening"""
    from core.listening_scope import SHARED_OWNER
    try:
        piles = sharing_piles(database)
    except Exception as e:  # noqa: BLE001 - can't tell: the shared pile only
        logger.debug("sharing piles lookup failed: %s", e)
        piles = set()
    return sorted({SHARED_OWNER} | piles)


__all__ = [
    "PRIVATE_SESSION_HOURS", "ensure_schema", "get_privacy", "set_share_listening",
    "mark_asked", "set_private_session", "in_private_session", "sharing_piles",
    "global_shelf_piles",
]
