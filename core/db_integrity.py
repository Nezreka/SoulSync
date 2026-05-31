"""SQLite integrity + safe-backup helpers.

Born out of a real incident: a WAL-mode DB got corrupted (most likely an
interrupted write during a hard restart), and because the backup routine
(a) never checked integrity and (b) rotated the oldest backup out by mtime,
every rolling backup ended up being a faithful copy of the already-corrupt
file — so when recovery was needed, all snapshots were poisoned.

This module makes that impossible:

* ``quick_check(path)`` / ``is_healthy(path)`` — fast read-only integrity probe.
* ``safe_backup(...)`` — verifies the SOURCE is healthy before copying, uses the
  SQLite Online Backup API, then verifies the RESULT. A corrupt source never
  produces (or keeps) a backup.
* ``prune_backups(...)`` — rotation that NEVER deletes the most recent
  *verified-healthy* backup, even to honor the max-count, so a run of bad
  backups can't evict your last good one.

Pure-ish: only touches sqlite3 + the filesystem paths it's given; no Flask, no
app globals. Unit-testable with real (and deliberately-corrupted) temp DBs.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger("db_integrity")


def _close_quietly(conn) -> None:
    """Best-effort close; a failure to close during cleanup must not mask the
    real error we're handling, but we log it rather than swallow silently."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception as e:  # noqa: BLE001 — cleanup path, real error already in flight
        logger.debug("db_integrity: connection close failed: %s", e)


class DBIntegrityError(Exception):
    """Raised when a database fails its integrity check."""


def quick_check(db_path: str, *, timeout: float = 30.0) -> str:
    """Run ``PRAGMA quick_check`` read-only and return its first result row.

    Returns ``'ok'`` for a healthy DB, otherwise the first error line. Raises
    ``DBIntegrityError`` if the file can't even be opened/read (malformed
    header, I/O error) — i.e. unambiguously bad.
    """
    if not os.path.exists(db_path):
        raise DBIntegrityError(f"Database file not found: {db_path}")
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
        row = conn.execute("PRAGMA quick_check(1)").fetchone()
        return (row[0] if row else "no result")
    except sqlite3.DatabaseError as e:
        # malformed header / disk image malformed / disk I/O error
        raise DBIntegrityError(f"{db_path}: {e}") from e
    finally:
        _close_quietly(conn)


def is_healthy(db_path: str, *, timeout: float = 30.0) -> bool:
    """True iff the DB opens and ``quick_check`` reports 'ok'. Never raises."""
    try:
        return quick_check(db_path, timeout=timeout) == "ok"
    except DBIntegrityError:
        return False


def safe_backup(src_path: str, dst_path: str, *, verify_source: bool = True,
                verify_result: bool = True) -> None:
    """Back up ``src_path`` to ``dst_path`` via the SQLite Online Backup API,
    refusing to produce a backup from (or keep a backup of) a corrupt DB.

    Raises ``DBIntegrityError`` and removes any partial ``dst_path`` when the
    source is unhealthy (``verify_source``) or the produced backup fails its
    own check (``verify_result``). On success ``dst_path`` is a verified-good
    copy.
    """
    if verify_source and not is_healthy(src_path):
        # Don't immortalize corruption — surface it so the caller can alert
        # and, crucially, NOT rotate out the existing good backups.
        raise DBIntegrityError(
            f"Refusing to back up: source database failed integrity check ({src_path})"
        )

    src = dst = None
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        src.backup(dst)
    finally:
        _close_quietly(dst)
        _close_quietly(src)

    if verify_result and not is_healthy(dst_path):
        # The copy itself came out bad — discard it rather than keep a dud.
        try:
            os.remove(dst_path)
        except OSError:
            pass
        raise DBIntegrityError(
            f"Backup produced a corrupt file and was discarded ({dst_path})"
        )


def prune_backups(backup_paths, max_keep: int,
                  health_check=is_healthy) -> list:
    """Decide which backups to delete to honor ``max_keep`` WITHOUT ever
    deleting the most-recent verified-healthy backup.

    ``backup_paths`` is an iterable of paths; order does not matter (we sort by
    mtime). Returns the list of paths that SHOULD be deleted (does not delete
    them — the caller does the IO, so this stays pure/testable).

    Rule: oldest-first deletion until <= max_keep, but the single newest
    *healthy* backup is protected and never selected for deletion. So even if
    the newest few backups are corrupt, the last good snapshot survives.
    """
    paths = [p for p in backup_paths]
    # Newest first.
    paths.sort(key=lambda p: _safe_mtime(p), reverse=True)

    # Find the newest healthy backup — the one we must never drop.
    protected: Optional[str] = None
    for p in paths:
        if health_check(p):
            protected = p
            break

    if len(paths) <= max_keep:
        return []

    # Delete oldest-first beyond max_keep, but skip the protected one.
    deletable = [p for p in paths if p != protected]
    # oldest first among deletable
    deletable.sort(key=lambda p: _safe_mtime(p))
    num_to_delete = len(paths) - max_keep
    return deletable[:num_to_delete]


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
