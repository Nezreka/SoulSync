"""Offline downloads tracking & per-user download permissions for /player.

Tracks every item downloaded on Desktop or Android (movies, episodes, YouTube,
podcasts, audiobooks, music tracks/albums) along with device identity, quality,
size, and deletion sync (when a user removes a download on their device).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VALID_TYPES = frozenset({"movie", "episode", "youtube", "home", "podcast", "audiobook", "track", "album"})

_DDL = """
CREATE TABLE IF NOT EXISTS player_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    device_id TEXT NOT NULL DEFAULT 'web',
    device_name TEXT NOT NULL DEFAULT 'Web Browser',
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    artwork TEXT,
    quality TEXT NOT NULL DEFAULT 'original',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'downloaded',
    downloaded_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_player_downloads_profile ON player_downloads(profile_id, status);
CREATE INDEX IF NOT EXISTS idx_player_downloads_item ON player_downloads(profile_id, item_type, item_id, device_id);

CREATE TABLE IF NOT EXISTS player_download_permissions (
    profile_id INTEGER PRIMARY KEY,
    allow_downloads INTEGER NOT NULL DEFAULT 1,
    max_items INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class DownloadLimitReached(ValueError):
    """the profile is at the admin's download cap"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure(conn) -> None:
    conn.executescript(_DDL)


def get_permission(db, profile_id: int) -> Dict[str, Any]:
    with db._get_connection() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT allow_downloads, max_items, updated_at FROM player_download_permissions WHERE profile_id = ?",
            (int(profile_id),),
        ).fetchone()
        if not row:
            return {"allow_downloads": True, "max_items": 0, "updated_at": None}
        return {
            "allow_downloads": bool(row["allow_downloads"]),
            "max_items": int(row["max_items"] or 0),
            "updated_at": row["updated_at"],
        }


def set_permission(db, profile_id: int, allow_downloads: bool, max_items: int = 0) -> Dict[str, Any]:
    now = _now_iso()
    allow_int = 1 if allow_downloads else 0
    cap = max(0, int(max_items or 0))
    with db._get_connection() as conn:
        _ensure(conn)
        conn.execute(
            """
            INSERT INTO player_download_permissions (profile_id, allow_downloads, max_items, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                allow_downloads = excluded.allow_downloads,
                max_items = excluded.max_items,
                updated_at = excluded.updated_at
            """,
            (int(profile_id), allow_int, cap, now),
        )
        conn.commit()
    return {"allow_downloads": bool(allow_int), "max_items": cap, "updated_at": now}


def log_download(
    db,
    profile_id: int,
    *,
    item_type: str,
    item_id: str,
    title: str,
    subtitle: Optional[str] = None,
    artwork: Optional[str] = None,
    device_id: str = "web",
    device_name: str = "Web Browser",
    quality: str = "original",
    size_bytes: int = 0,
    max_items: int = 0,
) -> Dict[str, Any]:
    """log one download. ``max_items`` (the admin's cap, 0 = none) counts
    what's still downloaded across the profile's devices; a new item past it
    is a DownloadLimitReached, a re-download of one already there is not."""
    itype = str(item_type or "").strip().lower()
    if itype not in VALID_TYPES:
        raise ValueError(f"Unsupported item_type: {item_type}")
    iid = str(item_id or "").strip()
    if not iid:
        raise ValueError("item_id is required")
    clean_title = str(title or "").strip() or f"{itype} {iid}"
    dev_id = str(device_id or "web").strip()[:80] or "web"
    dev_name = str(device_name or "Web Browser").strip()[:120] or "Web Browser"
    qual = str(quality or "original").strip()[:32] or "original"
    try:
        size = max(0, int(size_bytes or 0))
    except (TypeError, ValueError):
        size = 0
    now = _now_iso()

    with db._get_connection() as conn:
        _ensure(conn)
        existing = conn.execute(
            """
            SELECT id FROM player_downloads
            WHERE profile_id = ? AND device_id = ? AND item_type = ? AND item_id = ? AND status = 'downloaded'
            ORDER BY id DESC LIMIT 1
            """,
            (int(profile_id), dev_id, itype, iid),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE player_downloads
                SET title = ?, subtitle = ?, artwork = ?, device_name = ?,
                    quality = ?, size_bytes = ?, downloaded_at = ?, removed_at = NULL
                WHERE id = ?
                """,
                (clean_title, subtitle, artwork, dev_name, qual, size, now, int(existing["id"])),
            )
            row_id = int(existing["id"])
        else:
            if max_items and max_items > 0:
                held = conn.execute(
                    "SELECT COUNT(*) FROM player_downloads WHERE profile_id = ? AND status = 'downloaded'",
                    (int(profile_id),),
                ).fetchone()[0]
                if held >= max_items:
                    raise DownloadLimitReached(
                        f"You can keep {max_items} downloads. Remove one to make room."
                    )
            cur = conn.execute(
                """
                INSERT INTO player_downloads (
                    profile_id, device_id, device_name, item_type, item_id,
                    title, subtitle, artwork, quality, size_bytes, status, downloaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)
                """,
                (int(profile_id), dev_id, dev_name, itype, iid, clean_title, subtitle, artwork, qual, size, now),
            )
            row_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute("SELECT * FROM player_downloads WHERE id = ?", (row_id,)).fetchone()
        return dict(row)


def mark_removed(
    db,
    profile_id: int,
    *,
    download_id: Optional[int] = None,
    item_type: Optional[str] = None,
    item_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> int:
    now = _now_iso()
    with db._get_connection() as conn:
        _ensure(conn)
        if download_id is not None:
            cur = conn.execute(
                """
                UPDATE player_downloads
                SET status = 'removed', removed_at = ?
                WHERE id = ? AND profile_id = ? AND status = 'downloaded'
                """,
                (now, int(download_id), int(profile_id)),
            )
        elif item_type and item_id:
            if device_id:
                cur = conn.execute(
                    """
                    UPDATE player_downloads
                    SET status = 'removed', removed_at = ?
                    WHERE profile_id = ? AND item_type = ? AND item_id = ? AND device_id = ? AND status = 'downloaded'
                    """,
                    (now, int(profile_id), str(item_type).lower(), str(item_id), str(device_id)),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE player_downloads
                    SET status = 'removed', removed_at = ?
                    WHERE profile_id = ? AND item_type = ? AND item_id = ? AND status = 'downloaded'
                    """,
                    (now, int(profile_id), str(item_type).lower(), str(item_id)),
                )
        else:
            return 0
        conn.commit()
        return cur.rowcount


def list_for_profile(db, profile_id: int, *, include_removed: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
    with db._get_connection() as conn:
        _ensure(conn)
        if include_removed:
            rows = conn.execute(
                "SELECT * FROM player_downloads WHERE profile_id = ? ORDER BY downloaded_at DESC, id DESC LIMIT ?",
                (int(profile_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM player_downloads WHERE profile_id = ? AND status = 'downloaded' ORDER BY downloaded_at DESC, id DESC LIMIT ?",
                (int(profile_id), int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]


def admin_overview(
    db,
    profiles: List[Dict[str, Any]],
    *,
    filter_profile_id: Optional[int] = None,
    filter_type: Optional[str] = None,
    filter_status: Optional[str] = None,
    limit: int = 250,
) -> Dict[str, Any]:
    with db._get_connection() as conn:
        _ensure(conn)
        perms = {
            int(r["profile_id"]): {
                "allow_downloads": bool(r["allow_downloads"]),
                "max_items": int(r["max_items"] or 0),
                "updated_at": r["updated_at"],
            }
            for r in conn.execute("SELECT * FROM player_download_permissions").fetchall()
        }
        stats_rows = conn.execute(
            """
            SELECT
                profile_id,
                SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN status = 'removed' THEN 1 ELSE 0 END) AS removed_count,
                SUM(CASE WHEN status = 'downloaded' THEN size_bytes ELSE 0 END) AS active_bytes,
                COUNT(DISTINCT CASE WHEN status = 'downloaded' THEN device_id END) AS active_devices,
                MAX(downloaded_at) AS last_download_at
            FROM player_downloads
            GROUP BY profile_id
            """
        ).fetchall()
        stats = {int(r["profile_id"]): dict(r) for r in stats_rows}

        profile_names = {}
        users_out = []
        for p in profiles:
            pid = int(p.get("id"))
            name = p.get("display_name") or p.get("name") or f"User {pid}"
            profile_names[pid] = name
            st = stats.get(pid, {})
            pm = perms.get(pid, {"allow_downloads": True, "max_items": 0, "updated_at": None})
            users_out.append({
                "id": pid,
                "name": name,
                "is_admin": bool(p.get("is_admin")),
                "avatar_color": p.get("avatar_color"),
                "allow_downloads": pm["allow_downloads"],
                "max_items": pm["max_items"],
                "active_downloads": int(st.get("active_count") or 0),
                "removed_downloads": int(st.get("removed_count") or 0),
                "active_bytes": int(st.get("active_bytes") or 0),
                "active_devices": int(st.get("active_devices") or 0),
                "last_download_at": st.get("last_download_at"),
            })

        clauses = []
        params: List[Any] = []
        if filter_profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(int(filter_profile_id))
        if filter_type and filter_type in VALID_TYPES:
            clauses.append("item_type = ?")
            params.append(filter_type)
        if filter_status in ("downloaded", "removed"):
            clauses.append("status = ?")
            params.append(filter_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(500, int(limit))))
        dl_rows = conn.execute(
            f"SELECT * FROM player_downloads {where} ORDER BY downloaded_at DESC, id DESC LIMIT ?",  # noqa: S608
            params,
        ).fetchall()
        downloads_out = []
        for r in dl_rows:
            d = dict(r)
            d["profile_name"] = profile_names.get(int(d["profile_id"]), f"User {d['profile_id']}")
            downloads_out.append(d)

        return {
            "users": users_out,
            "downloads": downloads_out,
            "totals": {
                "active_downloads": sum(u["active_downloads"] for u in users_out),
                "removed_downloads": sum(u["removed_downloads"] for u in users_out),
                "active_bytes": sum(u["active_bytes"] for u in users_out),
            },
        }
