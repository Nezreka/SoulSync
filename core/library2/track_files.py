"""Multi-file model for Library v2 track files (audit P1-07 / ADR-03).

``lib2_track_files`` has always allowed several files per track (FLAC + MP3 of
the same recording, or old + new file mid-upgrade), but until ADR-03 every
reading path picked ``ORDER BY id LIMIT 1`` — the OLDEST row, regardless of
quality or state. This module gives the multi-file schema an actual model:

- ``is_primary``: exactly one file per track is the one the app acts on
  (wishlist mirror, quality eval, retag, artwork, duplicate view, move).
- ``file_state``: ``active`` / ``missing_suspected`` / ``missing_confirmed`` /
  ``quarantined`` / ``deleted`` — the lifecycle from ADR-03/P2-02. Non-active
  files stay visible but never win primary selection over an active one.

Primary selection strategy (the ADR requires ONE documented strategy, not
implicit code):

1. ``active`` files before any other state;
2. lossless formats (flac/alac/ape/wav/aiff) before lossy;
3. higher bit depth, then higher sample rate, then higher bitrate;
4. the NEWER row wins ties (highest id) — the exact opposite of the old
   accidental "oldest row" behaviour, because a newer import of equal quality
   is the fresher, more trustworthy copy.

Maintenance is automatic: triggers keep the invariant on insert, re-home
(track_id update) and delete, so every write path — importer, scan, autolink,
manual import — participates without changes. ``backfill_primary_flags`` runs
from the schema-ensure step and repairs installs that predate the columns.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.track_files")

FILE_STATES = ("active", "missing_suspected", "missing_confirmed",
               "quarantined", "deleted")

_LOSSLESS_FORMATS = "('flac','alac','ape','wav','aiff')"


def quality_order(alias: str = "") -> str:
    """The documented "best file" ordering WITHOUT the primary flag.

    Used to elect a primary (backfill, promotion after delete). Legacy rows
    with NULL ``file_state`` count as active.
    """
    p = f"{alias}." if alias else ""
    return (
        f"CASE WHEN COALESCE({p}file_state,'active')='active' THEN 0 ELSE 1 END, "
        f"CASE WHEN lower(COALESCE({p}format,'')) IN {_LOSSLESS_FORMATS} THEN 0 ELSE 1 END, "
        f"COALESCE({p}bit_depth,0) DESC, "
        f"COALESCE({p}sample_rate,0) DESC, "
        f"COALESCE({p}bitrate,0) DESC, "
        f"{p}id DESC"
    )


def primary_order(alias: str = "") -> str:
    """Read-path ordering: the primary flag first, quality as the defensive
    fallback for rows written before the flag existed (pre-backfill)."""
    p = f"{alias}." if alias else ""
    return f"{p}is_primary DESC, {quality_order(alias)}"


def primary_file_row(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """The track's primary file row (dict), or None when it has no file."""
    row = conn.execute(
        f"SELECT * FROM lib2_track_files WHERE track_id=? "
        f"ORDER BY {primary_order()} LIMIT 1",
        (int(track_id),),
    ).fetchone()
    return dict(row) if row else None


def primary_file_rows(conn, track_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Load each track's ADR-03 primary file in one query."""
    ids = sorted({int(track_id) for track_id in track_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT * FROM (
                SELECT tf.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY tf.track_id
                           ORDER BY {primary_order('tf')}
                       ) AS lib2_primary_rank
                  FROM lib2_track_files tf
                 WHERE tf.track_id IN ({marks})
            ) ranked
            WHERE lib2_primary_rank=1""",
        ids,
    ).fetchall()
    return {int(row["track_id"]): dict(row) for row in rows}


def set_primary_file(conn, track_id: int, file_id: int) -> bool:
    """Explicitly make ``file_id`` the track's primary file.

    Returns False when the file doesn't belong to the track. Does not commit.
    """
    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (int(file_id),)
    ).fetchone()
    if not owner or owner[0] != int(track_id):
        return False
    conn.execute(
        "UPDATE lib2_track_files SET is_primary=0 "
        "WHERE track_id=? AND is_primary=1 AND id<>?",
        (int(track_id), int(file_id)))
    conn.execute(
        "UPDATE lib2_track_files SET is_primary=1, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND is_primary=0", (int(file_id),))
    return True


def set_file_state(conn, file_id: int, state: str) -> bool:
    """Move a file through its ADR-03 lifecycle state.

    A primary file leaving ``active`` hands the flag to the best remaining
    active sibling (if any) so read paths keep acting on a live file.
    Returns False for unknown files/states. Does not commit.
    """
    if state not in FILE_STATES:
        return False
    row = conn.execute(
        "SELECT track_id, is_primary FROM lib2_track_files WHERE id=?",
        (int(file_id),)).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE lib2_track_files SET file_state=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?", (state, int(file_id)))
    track_id = row[0]
    if track_id is not None and row[1] and state != "active":
        replacement = conn.execute(
            f"""SELECT id FROM lib2_track_files
                 WHERE track_id=? AND id<>?
                   AND COALESCE(file_state,'active')='active'
                 ORDER BY {quality_order()} LIMIT 1""",
            (track_id, int(file_id))).fetchone()
        if replacement:
            conn.execute("UPDATE lib2_track_files SET is_primary=0 WHERE id=?",
                         (int(file_id),))
            conn.execute("UPDATE lib2_track_files SET is_primary=1 WHERE id=?",
                         (replacement[0],))
    return True


def backfill_primary_flags(cursor) -> int:
    """Repair/seed the one-primary-per-track invariant. Idempotent.

    Promotes the best file of every track that has files but no primary and
    demotes accidental extra primaries (keeping the best). Orphaned rows
    (``track_id IS NULL``) never carry the flag. Returns rows changed.
    """
    changed = 0
    cursor.execute(
        "UPDATE lib2_track_files SET is_primary=0 "
        "WHERE track_id IS NULL AND is_primary=1")
    changed += cursor.rowcount
    # Keep only the best primary where several are flagged.
    cursor.execute(f"""
        UPDATE lib2_track_files SET is_primary=0
         WHERE is_primary=1 AND track_id IS NOT NULL
           AND id NOT IN (
               SELECT (SELECT f.id FROM lib2_track_files f
                        WHERE f.track_id = t.track_id AND f.is_primary=1
                        ORDER BY {quality_order('f')} LIMIT 1)
                 FROM (SELECT DISTINCT track_id FROM lib2_track_files
                        WHERE is_primary=1 AND track_id IS NOT NULL) t)
    """)
    changed += cursor.rowcount
    # Elect a primary where none exists.
    cursor.execute(f"""
        UPDATE lib2_track_files SET is_primary=1
         WHERE id IN (
               SELECT (SELECT f.id FROM lib2_track_files f
                        WHERE f.track_id = t.track_id
                        ORDER BY {quality_order('f')} LIMIT 1)
                 FROM (SELECT DISTINCT track_id FROM lib2_track_files
                        WHERE track_id IS NOT NULL) t
                WHERE NOT EXISTS (
                      SELECT 1 FROM lib2_track_files p
                       WHERE p.track_id = t.track_id AND p.is_primary=1))
    """)
    changed += cursor.rowcount
    return changed


def install_primary_triggers(cursor) -> None:
    """(Re)install the invariant-keeping triggers. Idempotent.

    - insert: a new file becomes primary iff its track has none yet;
    - move (track_id update): the row keeps/gets primary on the target only
      if the target has none, and the source track elects a new primary;
    - delete: a deleted primary hands the flag to the best remaining sibling.
    """
    for name in ("insert", "move", "delete"):
        cursor.execute(
            f"DROP TRIGGER IF EXISTS trg_lib2_track_files_primary_{name}")
    cursor.execute("""
        CREATE TRIGGER trg_lib2_track_files_primary_insert
        AFTER INSERT ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.track_id IS NOT NULL AND NEW.is_primary=0
         AND NOT EXISTS (SELECT 1 FROM lib2_track_files
                          WHERE track_id=NEW.track_id AND is_primary=1
                            AND id<>NEW.id)
        BEGIN
            UPDATE lib2_track_files SET is_primary=1 WHERE id=NEW.id;
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_move
        AFTER UPDATE OF track_id ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.track_id IS NOT NULL
        BEGIN
            UPDATE lib2_track_files SET is_primary =
                CASE WHEN EXISTS (SELECT 1 FROM lib2_track_files
                                   WHERE track_id=NEW.track_id AND is_primary=1
                                     AND id<>NEW.id)
                     THEN 0 ELSE 1 END
             WHERE id=NEW.id;
            UPDATE lib2_track_files SET is_primary=1
             WHERE OLD.track_id IS NOT NULL AND OLD.track_id<>NEW.track_id
               AND id=(SELECT id FROM lib2_track_files
                        WHERE track_id=OLD.track_id
                        ORDER BY {quality_order()} LIMIT 1)
               AND NOT EXISTS (SELECT 1 FROM lib2_track_files
                                WHERE track_id=OLD.track_id AND is_primary=1);
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_delete
        AFTER DELETE ON lib2_track_files
        FOR EACH ROW
        WHEN OLD.is_primary=1 AND OLD.track_id IS NOT NULL
        BEGIN
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=(SELECT id FROM lib2_track_files
                        WHERE track_id=OLD.track_id
                        ORDER BY {quality_order()} LIMIT 1);
        END
    """)


__all__ = [
    "FILE_STATES",
    "backfill_primary_flags",
    "install_primary_triggers",
    "primary_file_row",
    "primary_order",
    "quality_order",
    "set_file_state",
    "set_primary_file",
]
