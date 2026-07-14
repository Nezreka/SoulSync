"""Re-read audio files' real properties into ``lib2_track_files``.

The importer seeds file rows from the legacy DB, which only reliably knows
format+bitrate. "Refresh & Scan" calls this to probe each file on disk
(``core/imports/file_ops.probe_audio_quality`` — mutagen, ground truth) so
sample-rate/bit-depth-based quality targets (hi-res FLAC tiers) evaluate
against real values instead of format-based fallbacks.

The same pass refreshes the tag/gap cache through ``core.tag_writer``'s
canonical reader. Tag and quality probes are independent: failure of one must
not keep the other stale.

Files whose path does not exist are left untouched (bind mounts can be
temporarily absent in Docker; a missing path is not proof the file is gone).
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.scan")

ProgressCb = Optional[Callable[[str, int, int], None]]


def _file_rows_in_scope(conn, *, album_ids: Optional[List[int]] = None) -> List[Any]:
    # Scope contract: None = whole library, [] = nothing. An empty scope must
    # never widen to a full-library scan (an artist without albums would
    # otherwise probe every file in the database).
    if album_ids is not None:
        if not album_ids:
            return []
        marks = ",".join("?" for _ in album_ids)
        return conn.execute(
            f"""SELECT tf.id, tf.path FROM lib2_track_files tf
                JOIN lib2_tracks t ON t.id = tf.track_id
               WHERE t.album_id IN ({marks}) AND tf.path IS NOT NULL AND tf.path <> ''""",
            album_ids,
        ).fetchall()
    return conn.execute(
        "SELECT id, path FROM lib2_track_files WHERE path IS NOT NULL AND path <> ''"
    ).fetchall()


def rescan_files(database, *, album_ids: Optional[List[int]] = None,
                 progress: ProgressCb = None) -> Dict[str, int]:
    """Probe the files in scope and persist their measured audio properties.

    ``album_ids=None`` scans the whole library; an empty list scans nothing.

    Returns ``{"scanned": n, "updated": n, "missing": n}``. Never raises for
    individual files — a broken file just stays on its imported values.

    Stored paths are the legacy DB's (often the media server's) view of the
    filesystem, so each one goes through the shared resolver — on path-mapped
    setups the raw path never exists here and a raw ``os.path.exists`` check
    would report the whole library "missing".
    """
    from core.imports.file_ops import probe_audio_quality
    from core.library2.paths import resolve_lib2_path
    from core.library2.status import quality_tier
    from core.library2.tag_cache import read_and_persist_tag_cache

    stats = {"scanned": 0, "updated": 0, "missing": 0}
    conn = database._get_connection()
    try:
        rows = _file_rows_in_scope(conn, album_ids=album_ids)
        total = len(rows)
        for i, row in enumerate(rows):
            path = resolve_lib2_path(row["path"])
            if progress and i % 25 == 0:
                progress("scan", i, total)
            if not path:
                stats["missing"] += 1
                continue
            stats["scanned"] += 1
            read_and_persist_tag_cache(conn, row["id"], path)
            try:
                quality = probe_audio_quality(path)
            except Exception as e:  # noqa: BLE001
                logger.debug("probe failed (%s): %s", path, e)
                continue
            if quality is None:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            tier = quality_tier(quality.format, quality.bitrate, quality.bit_depth)
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
                (quality.format, quality.bitrate, quality.sample_rate,
                 quality.bit_depth, size, tier, row["id"]),
            )
            stats["updated"] += 1
        conn.commit()
    finally:
        conn.close()
    logger.info("Library v2 file rescan: %(scanned)d probed, %(updated)d updated, "
                "%(missing)d paths absent", stats)
    return stats


__all__ = ["rescan_files"]
