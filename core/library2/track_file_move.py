"""Move a file link between two Library-v2 track rows (single ↔ album).

The missing third option next to "dedup (delete the single's file)" and
"unlink (they're not the same recording)": the user has ONE physical file
attached to the wrong release variant — re-home it without re-downloading or
deleting anything.

What moves is the ``lib2_track_files`` row (the DB's file↔track link). The
file on disk is NOT touched: renaming/refoldering to the target release's
naming scheme is the reorganize job's business, which reads the corrected
library state afterwards. That keeps this operation instant, reversible and
safe on bind mounts.

After the move the SOURCE track has no files (shows "missing" again). To keep
the pipeline from immediately re-downloading the variant the user just
consolidated away, the source track is unmonitored and its wishlist mirror is
withdrawn — an explicit monitor toggle re-wants it any time.
"""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.track_file_move")


class MoveError(ValueError):
    """Validation failure with a user-facing message and an HTTP-ish status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def move_track_file(db, conn, from_track_id: int, to_track_id: int,
                    *, wishlist_profile_id: int = 1) -> Dict[str, Any]:
    """Re-home ``from_track_id``'s file link onto ``to_track_id``.

    All source file rows move as one set so multi-file tracks cannot be left
    half-consolidated. Returns the primary ``moved_file_id`` for compatibility
    plus ``moved_file_ids`` / ``moved_file_count``. Raises :class:`MoveError`
    on validation failure. The caller
    owns no transaction state — this commits on success.
    """
    from core.library2.duplicate_relationship import (
        DuplicateRelationshipError,
        validate_duplicate_pair,
    )
    try:
        pair = validate_duplicate_pair(
            conn,
            from_track_id,
            to_track_id,
            allow_reverse_existing=True,
        )
    except DuplicateRelationshipError as exc:
        raise MoveError(str(exc), status=exc.status) from exc
    src = pair["source"]
    dst = pair["target"]

    # The primary row leads the compatibility response, but every sibling
    # moves with it so the source cannot be left half-consolidated. Schema
    # triggers keep exactly one primary on the target throughout the UPDATE.
    from core.library2.track_files import primary_order
    file_rows = conn.execute(
        f"SELECT id, path FROM lib2_track_files WHERE track_id=? "
        f"ORDER BY {primary_order()}",
        (from_track_id,),
    ).fetchall()
    if not file_rows or not any(str(row["path"] or "").strip() for row in file_rows):
        raise MoveError("Source track has no file to move", status=409)

    target_has_file = conn.execute(
        "SELECT 1 FROM lib2_track_files "
        "WHERE track_id=? LIMIT 1",
        (to_track_id,),
    ).fetchone()
    if target_has_file:
        raise MoveError(
            "Target track already has a file — remove or dedup it first", status=409)

    conn.execute(
        """UPDATE lib2_track_files
              SET track_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE track_id=?""",
        (to_track_id, from_track_id),
    )
    if pair["reverse_existing"]:
        # Keep the file-owning side canonical. Clearing first avoids a
        # transient two-node cycle and journals the reversal truthfully.
        conn.execute(
            """UPDATE lib2_tracks
                  SET canonical_track_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (to_track_id,),
        )
        conn.execute(
            """UPDATE lib2_tracks
                  SET canonical_track_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (to_track_id, from_track_id),
        )
    # The source just lost its file on purpose; stop wanting it so the
    # pipeline doesn't instantly re-download the consolidated-away variant.
    from core.library2.wanted import recompute_wanted, track_is_wanted
    try:
        source_unmonitored = track_is_wanted(
            conn, from_track_id, profile_id=wishlist_profile_id
        )
    except RuntimeError:
        # Upgrade path for rows created before the projection was complete.
        recompute_wanted(conn, track_ids=[from_track_id])
        source_unmonitored = track_is_wanted(
            conn, from_track_id, profile_id=wishlist_profile_id
        )
    conn.execute("UPDATE lib2_tracks SET monitored=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (from_track_id,))
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    record_rule(
        conn,
        "track",
        from_track_id,
        False,
        PROVENANCE_USER,
        profile_id=wishlist_profile_id,
    )
    recompute_wanted(conn, track_ids=[from_track_id])
    conn.commit()

    unmirrored = 0
    if source_unmonitored:
        try:
            from core.library2.wishlist_mirror import (
                mirror_projected_tracks_wishlist,
            )
            unmirrored = mirror_projected_tracks_wishlist(
                db,
                conn,
                [from_track_id],
                profile_id=wishlist_profile_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("wishlist unmirror after move failed (track %s): %s",
                         from_track_id, e)

    moved_file_ids = [int(row["id"]) for row in file_rows]
    logger.info("Moved %s file links %s: track %s ('%s') → track %s ('%s')",
                len(moved_file_ids), moved_file_ids, from_track_id, src["title"],
                to_track_id, dst["title"])
    return {
        "moved_file_id": moved_file_ids[0],
        "moved_file_ids": moved_file_ids,
        "moved_file_count": len(moved_file_ids),
        "file_path": file_rows[0]["path"],
        "file_paths": [row["path"] for row in file_rows],
        "from_track_id": from_track_id,
        "to_track_id": to_track_id,
        "source_unmonitored": source_unmonitored,
        "unmirrored": unmirrored,
        "canonical_reversed": bool(pair["reverse_existing"]),
    }


__all__ = ["move_track_file", "MoveError"]
