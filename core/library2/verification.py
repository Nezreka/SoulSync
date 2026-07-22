"""Keep Library-v2 file verification state aligned with review actions."""

from __future__ import annotations

import os
from typing import Any, Iterable


def mark_file_verification_status(
    conn: Any,
    paths: Iterable[str],
    status: str,
    *,
    config_manager: Any = None,
) -> int:
    """Update every lib2 file row resolving to one of ``paths``.

    Library v2 is optional and stored paths may use a media-server/container
    prefix, so raw SQL equality is only the fast path. The resolver comparison
    closes mapped-path setups without making verification approval depend on
    Library v2 being enabled.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='lib2_track_files'"
    ).fetchone()
    if not exists:
        return 0

    candidates = {
        os.path.normcase(os.path.abspath(str(path)))
        for path in paths
        if path
    }
    if not candidates:
        return 0

    updated_ids: set[int] = set()
    rows = conn.execute(
        "SELECT id, path FROM lib2_track_files "
        "WHERE path IS NOT NULL AND path != ''"
    ).fetchall()
    from core.library2.paths import resolve_lib2_path

    for row in rows:
        raw_path = str(row["path"])
        raw_norm = os.path.normcase(os.path.abspath(raw_path))
        matches = raw_norm in candidates
        if not matches:
            resolved = resolve_lib2_path(raw_path, config_manager=config_manager)
            if resolved:
                resolved_norm = os.path.normcase(os.path.abspath(str(resolved)))
                matches = resolved_norm in candidates
        if matches:
            updated_ids.add(int(row["id"]))

    if updated_ids:
        marks = ",".join("?" for _ in updated_ids)
        conn.execute(
            f"UPDATE lib2_track_files SET verification_status=?, "
            f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})",
            (status, *sorted(updated_ids)),
        )
    return len(updated_ids)


__all__ = ["mark_file_verification_status"]
