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

After the move the SOURCE track has no file (shows "missing" again). To keep
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

    Returns ``{moved_file_id, from_track_id, to_track_id, source_unmonitored,
    unmirrored}``. Raises :class:`MoveError` on validation failure. The caller
    owns no transaction state — this commits on success.
    """
    if from_track_id == to_track_id:
        raise MoveError("Source and target are the same track")

    src = conn.execute(
        "SELECT id, title, monitored FROM lib2_tracks WHERE id=?", (from_track_id,)
    ).fetchone()
    if not src:
        raise MoveError("Source track not found", status=404)
    dst = conn.execute(
        "SELECT id, title FROM lib2_tracks WHERE id=?", (to_track_id,)
    ).fetchone()
    if not dst:
        raise MoveError("Target track not found", status=404)

    # Move the PRIMARY file (ADR-03), not an arbitrary sibling copy; the
    # schema triggers re-elect primaries on both tracks after the UPDATE.
    from core.library2.track_files import primary_order
    file_row = conn.execute(
        f"SELECT id, path FROM lib2_track_files "
        f"WHERE track_id=? AND path IS NOT NULL AND path <> '' "
        f"ORDER BY {primary_order()} LIMIT 1",
        (from_track_id,),
    ).fetchone()
    if not file_row:
        raise MoveError("Source track has no file to move", status=409)

    target_has_file = conn.execute(
        "SELECT 1 FROM lib2_track_files "
        "WHERE track_id=? AND path IS NOT NULL AND path <> '' LIMIT 1",
        (to_track_id,),
    ).fetchone()
    if target_has_file:
        raise MoveError(
            "Target track already has a file — remove or dedup it first", status=409)

    conn.execute(
        "UPDATE lib2_track_files SET track_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (to_track_id, file_row["id"]),
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

    logger.info("Moved file link %s: track %s ('%s') → track %s ('%s')",
                file_row["id"], from_track_id, src["title"], to_track_id, dst["title"])
    return {
        "moved_file_id": file_row["id"],
        "file_path": file_row["path"],
        "from_track_id": from_track_id,
        "to_track_id": to_track_id,
        "source_unmonitored": source_unmonitored,
        "unmirrored": unmirrored,
    }


__all__ = ["move_track_file", "MoveError"]
