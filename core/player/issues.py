"""Player-level media issue reporting & admin triage (all media types).

Allows any signed-in profile (Desktop `/player` or paired Android device) to flag
corrupted files, sync/audio/subtitle problems, or wrong metadata on any movie,
episode, YouTube video, music track, podcast episode, audiobook track, or
commercial. Automatically captures the resolved server file path when available
so admins know the exact file on disk to inspect or re-download.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

ALLOWED_ITEM_TYPES = frozenset({
    "movie",
    "episode",
    "youtube",
    "track",
    "podcast",
    "podcast_episode",
    "audiobook",
    "music_video",
    "commercial",
})

ALLOWED_CATEGORIES = frozenset({
    "playback",
    "audio",
    "subtitles",
    "metadata",
    "quality",
    "other",
})

ALLOWED_STATUSES = frozenset({"open", "resolved", "dismissed"})

_DDL = """
CREATE TABLE IF NOT EXISTS player_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    profile_name TEXT NOT NULL DEFAULT '',
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    subtitle TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'playback',
    note TEXT NOT NULL DEFAULT '',
    position_sec INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    resolved_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_player_issues_status_created
    ON player_issues(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_player_issues_profile
    ON player_issues(profile_id, created_at DESC);
"""


def _ensure(db) -> None:
    with db._get_connection() as conn:
        conn.executescript(_DDL)
        conn.commit()


def _resolve_file_path(item_type: str, item_id: str) -> str:
    """where the admin should look: the file on disk, or which kind of source.
    a relayed or external url is never stored: a media server's carries its
    token (?api_key=, ?X-Plex-Token=), and this row goes back to the reporter."""
    try:
        from core.player.playable import resolve
        src = resolve(item_type, item_id, None)
        if src.mode == "file" and src.path:
            return str(src.path)[:1000]
        return f"({src.mode} stream)" if src.mode else ""
    except Exception:  # noqa: BLE001
        return ""


def _safe_file_path(value: Any) -> str:
    """rows stored before the fix may hold a token url: show only that it was one"""
    text = str(value or "")
    if "://" in text:
        return "(relay stream)"
    return text


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": int(row[0]),
        "profile_id": int(row[1]),
        "profile_name": row[2] or "",
        "item_type": row[3] or "",
        "item_id": row[4] or "",
        "title": row[5] or "",
        "subtitle": row[6] or "",
        "file_path": _safe_file_path(row[7]),
        "category": row[8] or "playback",
        "note": row[9] or "",
        "position_sec": int(row[10] or 0),
        "status": row[11] or "open",
        "admin_note": row[12] or "",
        "created_at": int(row[13] or 0),
        "resolved_at": int(row[14]) if row[14] is not None else None,
    }


def create_issue(
    db,
    *,
    profile_id: int,
    profile_name: str,
    item_type: str,
    item_id: str,
    title: str = "",
    subtitle: str = "",
    category: str = "playback",
    note: str = "",
    position_sec: int = 0,
) -> Dict[str, Any]:
    _ensure(db)
    itype = str(item_type or "").strip().lower()
    iid = str(item_id or "").strip()
    if itype not in ALLOWED_ITEM_TYPES or not iid:
        raise ValueError("Valid item_type and item_id are required.")
    cat = str(category or "playback").strip().lower()
    if cat not in ALLOWED_CATEGORIES:
        cat = "other"
    clean_title = str(title or "").strip()[:200] or f"{itype} {iid}"
    clean_sub = str(subtitle or "").strip()[:240]
    clean_note = str(note or "").strip()[:1000]
    pos = max(0, int(position_sec or 0))
    file_path = _resolve_file_path(itype, iid)
    now = int(time.time())
    with db._get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO player_issues
                (profile_id, profile_name, item_type, item_id, title, subtitle,
                 file_path, category, note, position_sec, status, admin_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', '', ?)
            """,
            (
                int(profile_id),
                str(profile_name or "").strip()[:120],
                itype,
                iid,
                clean_title,
                clean_sub,
                file_path,
                cat,
                clean_note,
                pos,
                now,
            ),
        )
        issue_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute(
            """
            SELECT id, profile_id, profile_name, item_type, item_id, title, subtitle,
                   file_path, category, note, position_sec, status, admin_note, created_at, resolved_at
            FROM player_issues WHERE id = ?
            """,
            (issue_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_issues(
    db,
    *,
    profile_id: int,
    is_admin: bool,
    status: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    _ensure(db)
    lim = max(1, min(200, int(limit or 100)))
    clauses: List[str] = []
    params: List[Any] = []
    if not is_admin:
        clauses.append("profile_id = ?")
        params.append(int(profile_id))
    if status and status in ALLOWED_STATUSES:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db._get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, profile_id, profile_name, item_type, item_id, title, subtitle,
                   file_path, category, note, position_sec, status, admin_note, created_at, resolved_at
            FROM player_issues
            {where}
            ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC
            LIMIT ?
            """,
            (*params, lim),
        ).fetchall()
        if is_admin:
            open_count = int(conn.execute("SELECT COUNT(*) FROM player_issues WHERE status = 'open'").fetchone()[0])
        else:
            open_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM player_issues WHERE status = 'open' AND profile_id = ?",
                    (int(profile_id),),
                ).fetchone()[0]
            )
    return {
        "issues": [_row_to_dict(r) for r in rows],
        "open_count": open_count,
    }


def update_issue(
    db,
    issue_id: int,
    *,
    status: Optional[str] = None,
    admin_note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    _ensure(db)
    sets: List[str] = []
    params: List[Any] = []
    if status is not None:
        st = str(status).strip().lower()
        if st not in ALLOWED_STATUSES:
            raise ValueError("Invalid status.")
        sets.append("status = ?")
        params.append(st)
        if st in ("resolved", "dismissed"):
            sets.append("resolved_at = ?")
            params.append(int(time.time()))
        else:
            sets.append("resolved_at = NULL")
    if admin_note is not None:
        sets.append("admin_note = ?")
        params.append(str(admin_note).strip()[:1000])
    if not sets:
        raise ValueError("Nothing to update.")
    params.append(int(issue_id))
    with db._get_connection() as conn:
        conn.execute(f"UPDATE player_issues SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute(
            """
            SELECT id, profile_id, profile_name, item_type, item_id, title, subtitle,
                   file_path, category, note, position_sec, status, admin_note, created_at, resolved_at
            FROM player_issues WHERE id = ?
            """,
            (int(issue_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_issue(db, issue_id: int, *, profile_id: int, is_admin: bool) -> bool:
    _ensure(db)
    with db._get_connection() as conn:
        if is_admin:
            cur = conn.execute("DELETE FROM player_issues WHERE id = ?", (int(issue_id),))
        else:
            cur = conn.execute(
                "DELETE FROM player_issues WHERE id = ? AND profile_id = ?",
                (int(issue_id), int(profile_id)),
            )
        conn.commit()
        return cur.rowcount > 0
