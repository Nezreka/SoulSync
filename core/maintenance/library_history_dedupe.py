"""One-time cleanup of repeated scan imports in Library History.

3.6.1 wrote an ``import`` row every time a scan made a fresh track row, and a
full refresh made fresh rows for the whole library: one install had 705k
import rows for 256k files, some unchanged files logged 130+ times. Library v2
logs a scan import once, when the file is first taken in.

This removes the repeats: scan imports (no origin, source track, download
source, verification or acquisition link) whose file, title, artist, album
and server match an EARLIER scan import. The earliest stays, so each file
keeps the date it really arrived. Downloads and anything linked elsewhere are
never touched. One pass by id, a batch per transaction, a marker when done.
"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("maintenance.library_history_dedupe")

MARKER = "library_history_scan_import_dedupe"
_BATCH = 5000
_SCAN_ONLY = ("origin", "source_track_id", "download_source",
              "verification_status", "acquisition_request_id")


def _columns(conn) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(library_history)")}


def dedupe_scan_import_history(database: Any, *, should_stop=None) -> Dict[str, int]:
    stats = {"checked": 0, "removed": 0}
    with closing(database._get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='library_history'").fetchone():
            return stats
        conn.execute("CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
                      "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        if conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?", (MARKER,)).fetchone():
            return stats
        present = _columns(conn)
        blank = " AND ".join(f"{alias}.{col} IS NULL" for alias in ("h",)
                             for col in _SCAN_ONLY if col in present)
        blank = blank or "1 = 1"
        blank_o = blank.replace("h.", "o.")
        last = 0
        while True:
            if should_stop and should_stop():
                return stats  # no marker: the next start carries on
            window = conn.execute(
                "SELECT id FROM library_history WHERE id > ? ORDER BY id LIMIT ?",
                (last, _BATCH)).fetchall()
            if not window:
                break
            first, last = int(window[0][0]), int(window[-1][0])
            doomed: List[int] = [int(r[0]) for r in conn.execute(
                f"""SELECT h.id FROM library_history h
                     WHERE h.id BETWEEN ? AND ? AND h.event_type = 'import'
                       AND h.file_path IS NOT NULL AND {blank}
                       AND EXISTS (SELECT 1 FROM library_history o
                                    WHERE o.file_path = h.file_path AND o.id < h.id
                                      AND o.event_type = 'import' AND {blank_o}
                                      AND o.title IS h.title AND o.artist_name IS h.artist_name
                                      AND o.album_name IS h.album_name
                                      AND o.server_source IS h.server_source)""",
                (first, last))]
            stats["checked"] += len(window)
            if doomed:
                conn.execute("DELETE FROM library_history WHERE id IN "
                             "(SELECT value FROM json_each(?))", (json.dumps(doomed),))
                stats["removed"] += len(doomed)
            conn.commit()
        conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)", (MARKER,))
        conn.commit()
    if stats["removed"]:
        logger.info("Library History: removed %d repeated scan imports (%d rows checked)",
                    stats["removed"], stats["checked"])
    return stats


__all__ = ["dedupe_scan_import_history", "MARKER"]
