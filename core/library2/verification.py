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

    # iss29-E06: try the indexed equality match FIRST.
    #
    # This function used to read every `lib2_track_files` row unconditionally
    # and call `resolve_lib2_path` on each one that did not compare equal — and
    # that resolver starts with `os.path.exists` and suffix-walks every base
    # directory for anything missing. Called from the "Approve" endpoint, one
    # click on a 30k-file library over SMB/NFS meant 30k+ network stats; the
    # endpoint also ran it inside its open write transaction, so the single
    # SQLite writer was held for all of them (that half is fixed at the call
    # site). `idx_lib2_track_files_path` answers the ordinary case — the stored
    # path IS the path — with one index lookup and no filesystem access at all.
    path_list = sorted(candidates)
    for row in conn.execute(
        f"SELECT id, path FROM lib2_track_files WHERE path IN "
        f"({','.join('?' for _ in path_list)})",
        path_list,
    ).fetchall():
        updated_ids.add(int(row["id"]))

    if not updated_ids:
        # Fallback for mapped-path setups, where the stored path bears a
        # media-server/container prefix and only the resolver can relate it to
        # the on-disk path the caller has. Full fidelity is kept here on
        # purpose — the resolver, not this function, defines what "the same
        # file" means. It is reached only when the fast path matched nothing,
        # which on an ordinary setup is never.
        from core.library2.paths import resolve_lib2_path

        for row in conn.execute(
            "SELECT id, path FROM lib2_track_files "
            "WHERE path IS NOT NULL AND path != ''"
        ).fetchall():
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
