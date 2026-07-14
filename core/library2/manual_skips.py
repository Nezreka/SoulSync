"""Persist and consume user-approved Library-v2 check overrides."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Sequence


def record_manual_skip(
    database,
    *,
    content_key: str,
    title: Optional[str],
    artist: Optional[str],
    skipped_checks: Sequence[str],
    profile_id: int,
) -> Optional[int]:
    """Create the dispatch-time audit row; best-effort callers may ignore None."""
    checks = sorted({str(check).strip() for check in skipped_checks if str(check).strip()})
    if not checks:
        return None
    conn = database._get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO lib2_manual_skips(
                   content_key, title, artist, skipped_checks, profile_id, reason)
               VALUES(?,?,?,?,?, 'manual_download')""",
            (content_key, title, artist, json.dumps(checks), int(profile_id)),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def attach_manual_skip_file(database, *, content_key: str, file_path: Any) -> bool:
    """Bind the latest still-unbound dispatch audit to its final imported path."""
    path = str(file_path or "").strip()
    if not content_key or not path:
        return False
    conn = database._get_connection()
    try:
        row = conn.execute(
            """SELECT id FROM lib2_manual_skips
                WHERE content_key=? AND file_path IS NULL
                ORDER BY id DESC LIMIT 1""",
            (content_key,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE lib2_manual_skips SET file_path=? WHERE id=?",
            (path, row["id"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def active_skip_paths(
    conn,
    checks: Iterable[str],
    *,
    profile_id: int = 1,
) -> set[str]:
    """Final paths protected by an unacknowledged override for any check."""
    wanted = {str(check).strip() for check in checks if str(check).strip()}
    if not wanted:
        return set()
    rows = conn.execute(
        """SELECT file_path, skipped_checks FROM lib2_manual_skips
            WHERE profile_id=? AND acknowledged=0
              AND file_path IS NOT NULL AND file_path<>''""",
        (int(profile_id),),
    ).fetchall()
    paths = set()
    for row in rows:
        try:
            recorded = json.loads(row["skipped_checks"] or "[]")
        except (TypeError, ValueError):
            continue
        if isinstance(recorded, list) and wanted.intersection(map(str, recorded)):
            paths.add(str(row["file_path"]))
    return paths


def check_is_skipped(
    conn,
    file_paths: Iterable[Any],
    checks: Iterable[str],
    *,
    profile_id: int = 1,
) -> bool:
    candidates = {str(path) for path in file_paths if path}
    return bool(candidates.intersection(
        active_skip_paths(conn, checks, profile_id=profile_id)
    ))


__all__ = [
    "active_skip_paths",
    "attach_manual_skip_file",
    "check_is_skipped",
    "record_manual_skip",
]
