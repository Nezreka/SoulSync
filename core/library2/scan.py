"""Re-read audio files' real properties into ``lib2_track_files``.

The importer seeds file rows from the legacy DB, which only reliably knows
format+bitrate. "Refresh & Scan" calls this to probe each file on disk
(``core/imports/file_ops.probe_audio_quality`` — mutagen, ground truth) so
sample-rate/bit-depth-based quality targets (hi-res FLAC tiers) evaluate
against real values instead of format-based fallbacks.

The same pass refreshes the tag/gap cache through ``core.tag_writer``'s
canonical reader. Tag and quality probes are independent: failure of one must
not keep the other stale.

Missing paths advance only while their library root is known healthy: one
miss is suspected, two are confirmed. Unhealthy/unknown mounts defer the
transition, and a recovered path returns to active.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.scan")

ProgressCb = Optional[Callable[[str, int, int], None]]
MISSING_CONFIRMATION_SCANS = 2


def _file_rows_in_scope(
    conn,
    *,
    album_ids: Optional[List[int]] = None,
    file_ids: Optional[List[int]] = None,
) -> List[Any]:
    # Scope contract: None = whole library, [] = nothing. An empty scope must
    # never widen to a full-library scan (an artist without albums would
    # otherwise probe every file in the database).
    if album_ids is not None and file_ids is not None:
        raise ValueError("album_ids and file_ids are mutually exclusive")
    if file_ids is not None:
        if not file_ids:
            return []
        rows = []
        ids = list(dict.fromkeys(int(file_id) for file_id in file_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT id, path, size, file_state, missing_scan_count
                      FROM lib2_track_files
                     WHERE id IN ({marks}) AND path IS NOT NULL AND path <> ''
                       AND COALESCE(file_state,'active')<>'deleted'""",
                chunk,
            ).fetchall())
        return rows
    if album_ids is not None:
        if not album_ids:
            return []
        rows = []
        ids = list(dict.fromkeys(int(album_id) for album_id in album_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT tf.id, tf.path, tf.size, tf.file_state, tf.missing_scan_count
                      FROM lib2_track_files tf
                   JOIN lib2_tracks t ON t.id = tf.track_id
                   WHERE t.album_id IN ({marks}) AND tf.path IS NOT NULL AND tf.path <> ''
                     AND COALESCE(tf.file_state,'active')<>'deleted'""",
                chunk,
            ).fetchall())
        return rows
    return conn.execute(
        """SELECT id, path, size, file_state, missing_scan_count
             FROM lib2_track_files WHERE path IS NOT NULL AND path <> ''
              AND COALESCE(file_state,'active')<>'deleted'"""
    ).fetchall()


def _persist_missing_observation(
    database, file_id: int, *, root_healthy: bool, allow_confirm: bool = True,
) -> None:
    """Persist one missing-path observation in a short transaction.

    ``allow_confirm=False`` caps the row at ``missing_suspected`` no matter how
    many misses it has: pathdrift25-01: a stale *filename* leaves the parent
    directory perfectly healthy, so the miss looks credible and the row used to
    reach ``missing_confirmed`` while the song sat in that very folder under a
    slightly different name.  The miss is still counted — once the reconcile
    candidate disappears, the next scan confirms immediately.
    """
    if not root_healthy:
        return
    from core.library2.track_files import set_file_state

    conn = database._get_connection()
    try:
        row = conn.execute(
            "SELECT file_state, missing_scan_count FROM lib2_track_files WHERE id=?",
            (int(file_id),),
        ).fetchone()
        if not row or row["file_state"] not in (
            "active", "missing_suspected", "missing_confirmed"
        ):
            return
        misses = int(row["missing_scan_count"] or 0) + 1
        state = (
            "missing_confirmed"
            if misses >= MISSING_CONFIRMATION_SCANS and allow_confirm
            else "missing_suspected"
        )
        conn.execute(
            """UPDATE lib2_track_files
                  SET missing_scan_count=?,
                      missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP),
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (misses, int(file_id)),
        )
        set_file_state(conn, int(file_id), state)
        conn.commit()
    finally:
        conn.close()


def _persist_verification_observation(conn, file_id: int, file_tags: Dict[str, Any]) -> None:
    """Adopt the file's own ``SOULSYNC_VERIFICATION`` tag into the catalogue.

    Every file the download pipeline finalizes gets this tag written
    (``core/imports/pipeline._persist_verification_status``) precisely so the
    information survives a DB reset and travels with the file. The rescan
    already reads it — dropping it left ``lib2_track_files.verification_status``
    NULL for everything imported outside the autolink callback, so the UI had
    nothing to show (issues.md T-09).

    This is an *observation*, not a new judgement:

    - an unknown tag value is ignored (never invent a fifth state);
    - a file without the tag never clears a status the catalogue already has;
    - ``human_verified`` is a person's decision and outranks any machine
      state stamped into the file, so it is never overwritten.
    """
    from core.matching.verification_status import ALL_STATUSES, HUMAN_VERIFIED

    status = str(file_tags.get("verification_status") or "").strip()
    if status not in ALL_STATUSES:
        return
    conn.execute(
        """UPDATE lib2_track_files
              SET verification_status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND COALESCE(verification_status,'') NOT IN (?, ?)""",
        (status, int(file_id), status, HUMAN_VERIFIED),
    )


def _persist_present_observation(
    database,
    file_id: int,
    *,
    file_tags: Dict[str, Any],
    quality: Any = None,
    size: Optional[int] = None,
    tier: Optional[str] = None,
) -> bool:
    """Persist one completed file observation in a short transaction."""
    from core.library2.tag_cache import persist_tag_cache
    from core.library2.track_files import set_file_state

    conn = database._get_connection()
    try:
        row = conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE id=?", (int(file_id),)
        ).fetchone()
        if not row:
            return False
        if row["file_state"] in ("missing_suspected", "missing_confirmed"):
            set_file_state(conn, int(file_id), "active")
        conn.execute(
            """UPDATE lib2_track_files
                  SET missing_scan_count=0, missing_since=NULL
                WHERE id=? AND (missing_scan_count<>0 OR missing_since IS NOT NULL)""",
            (int(file_id),),
        )
        persist_tag_cache(conn, int(file_id), file_tags)
        _persist_verification_observation(conn, int(file_id), file_tags)
        if quality is not None:
            conn.execute(
                """UPDATE lib2_track_files SET
                       format = COALESCE(?, format),
                       bitrate = COALESCE(?, bitrate),
                       sample_rate = COALESCE(?, sample_rate),
                       bit_depth = COALESCE(?, bit_depth),
                       size = COALESCE(?, size),
                       quality_tier = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    quality.format,
                    quality.bitrate,
                    quality.sample_rate,
                    quality.bit_depth,
                    size,
                    tier,
                    int(file_id),
                ),
            )
        conn.commit()
        return quality is not None
    finally:
        conn.close()


def rescan_files(
    database,
    *,
    album_ids: Optional[List[int]] = None,
    file_ids: Optional[List[int]] = None,
    progress: ProgressCb = None,
) -> Dict[str, int]:
    """Probe the files in scope and persist their measured audio properties.

    ``album_ids=None`` and ``file_ids=None`` scan the whole library; an empty
    supplied list scans nothing.  The two scopes are mutually exclusive.  The
    file-id scope is used by repair/change bridges so one ReplayGain or lyrics
    approval never turns into a full-library probe.

    Returns ``{"scanned": n, "updated": n, "missing": n}``. Never raises for
    individual files — a broken file just stays on its imported values.

    Stored paths are the legacy DB's (often the media server's) view of the
    filesystem, so each one goes through the shared resolver — on path-mapped
    setups the raw path never exists here and a raw ``os.path.exists`` check
    would report the whole library "missing".
    """
    from core.imports.file_ops import probe_audio_quality
    from core.library2.path_drift import has_drift_candidate
    from core.library2.paths import (
        missing_path_root_is_healthy,
        resolve_lib2_path,
    )
    from core.library2.status import quality_tier
    from core.library2.tag_cache import read_tag_snapshot

    stats = {"scanned": 0, "updated": 0, "missing": 0, "path_drift": 0}
    conn = database._get_connection()
    try:
        # sqlite3.Row values remain tied to the result shape, so materialize
        # plain dicts before closing the read snapshot connection.
        rows = [
            dict(row)
            for row in _file_rows_in_scope(
                conn, album_ids=album_ids, file_ids=file_ids,
            )
        ]
    finally:
        conn.close()

    total = len(rows)
    for i, row in enumerate(rows):
        if progress and i % 25 == 0:
            progress("scan", i, total)
        path = resolve_lib2_path(row["path"])
        if not path:
            stats["missing"] += 1
            # pathdrift25-01: "unresolvable" and "gone" are not the same thing.
            # A drifted filename leaves a healthy directory holding the very
            # file this row describes; confirming that as missing hands a
            # present song to the wanted/redownload logic.  Only worth probing
            # when the root is healthy — an unhealthy one never advances the
            # lifecycle anyway, and on a fully unmounted library this would
            # otherwise double the failed resolves for every single row.
            root_healthy = missing_path_root_is_healthy(row["path"])
            drifted = root_healthy and has_drift_candidate(
                row["path"], expected_size=row.get("size"),
            )
            if drifted:
                stats["path_drift"] += 1
            _persist_missing_observation(
                database,
                row["id"],
                root_healthy=root_healthy,
                allow_confirm=not drifted,
            )
            continue

        stats["scanned"] += 1
        file_tags = read_tag_snapshot(path)
        try:
            quality = probe_audio_quality(path)
        except Exception as e:  # noqa: BLE001
            logger.debug("probe failed (%s): %s", path, e)
            quality = None
        size = None
        tier = None
        if quality is not None:
            try:
                size = os.path.getsize(path)
            except OSError:
                pass
            tier = quality_tier(quality.format, quality.bitrate, quality.bit_depth)
        if _persist_present_observation(
            database,
            row["id"],
            file_tags=file_tags,
            quality=quality,
            size=size,
            tier=tier,
        ):
            stats["updated"] += 1
    logger.info("Library v2 file rescan: %(scanned)d probed, %(updated)d updated, "
                "%(missing)d paths absent", stats)
    return stats


__all__ = ["MISSING_CONFIRMATION_SCANS", "rescan_files"]
