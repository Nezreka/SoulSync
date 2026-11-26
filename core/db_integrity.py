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

import contextlib
import logging
import os
import re
import sqlite3
import threading
from typing import Optional

logger = logging.getLogger("db_integrity")

# Pages per backup step when someone watches the copy: 4096 x 4 KiB = 16 MiB,
# a fresh number on the startup page every fraction of a second.
_BACKUP_STEP_PAGES = 4096


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
    # SQLite reads an empty file as a valid empty database and answers 'ok'.
    # A backup interrupted before its first page left exactly that, and the
    # rotation then protected the empty file as the newest healthy backup.
    if os.path.getsize(db_path) == 0:
        raise DBIntegrityError(f"{db_path}: empty file")
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


# a rolling backup's name: <db>.backup_YYYYMMDD_HHMMSS and nothing after it
_BACKUP_NAME = re.compile(r"\.backup_\d{8}_\d{6}$")
_SIDECARS = ("-wal", "-shm", ".meta.json")

# one backup or restore at a time, on either side: two in the same second
# share a destination name, and a scheduled backup must not run into a restore
backup_lock = threading.Lock()


def list_backups(db_path: str) -> list:
    """the rolling backups of ``db_path``: the backup files themselves, and
    nothing else that shares their prefix.

    every caller used to glob ``<db>.backup_*``, which also matches a
    backup's ``-wal`` / ``-shm`` sidecars and its ``.meta.json``. the rotation
    counted those as backups: with "keep 5" one backup and its sidecars were
    already three or four of the five, so each new backup evicted the real
    ones and only the newest ever survived."""
    folder = os.path.dirname(db_path) or "."
    prefix = os.path.basename(db_path) + ".backup_"
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(os.path.join(folder, n) for n in names
                  if n.startswith(prefix) and _BACKUP_NAME.search(n))


def remove_backup(path: str) -> None:
    """delete a backup and everything that belongs to it"""
    for target in (path, *(path + s for s in _SIDECARS)):
        with contextlib.suppress(OSError):
            os.remove(target)


def sweep_backup_leftovers(db_path: str) -> list:
    """delete the sidecars of backups that are gone (a ``-wal``, ``-shm`` or
    ``.meta.json`` whose backup file no longer exists). returns what went."""
    folder = os.path.dirname(db_path) or "."
    prefix = os.path.basename(db_path) + ".backup_"
    gone = []
    try:
        names = os.listdir(folder)
    except OSError:
        return gone
    have = set(names)
    for n in names:
        if not n.startswith(prefix):
            continue
        for side in _SIDECARS:
            if n.endswith(side) and _BACKUP_NAME.search(n[:-len(side)]) and n[:-len(side)] not in have:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(folder, n))
                    gone.append(n)
    return gone


def rotate_backups(db_path: str, max_keep: int) -> list:
    """keep the newest ``max_keep`` backups of ``db_path`` (never dropping the
    newest healthy one), delete the rest with their sidecars, and sweep up
    sidecars left by backups deleted before. returns the backups removed."""
    removed = []
    for path in prune_backups(list_backups(db_path), max_keep):
        remove_backup(path)
        removed.append(path)
    sweep_backup_leftovers(db_path)
    return removed


def schema_version(db_path: str) -> Optional[int]:
    """a database file's ``PRAGMA user_version``, None when it can't be read"""
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        _close_quietly(conn)


def room_for(path: str, need_bytes: int, *, margin: float = 1.05) -> Optional[str]:
    """None when the folder ``path`` is in has room for ``need_bytes``, else
    a sentence saying how short it is. can't tell is room: a backup is not
    refused because the free space couldn't be read."""
    import shutil
    folder = os.path.dirname(os.path.abspath(path)) or "."
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return None
    need = int(need_bytes * margin)
    if free >= need:
        return None
    gb = 1024 ** 3
    return (f"Not enough free space: this needs about {need / gb:.1f} GB "
            f"and the drive has {free / gb:.1f} GB free.")


def _make_self_contained(db_path: str) -> None:
    """leave a finished backup as ONE plain file. the backup api copies the
    source's header, so a backup of a WAL database is itself a WAL database:
    every later read of it (its health check, the rotation's) created
    ``-wal`` and ``-shm`` files beside it. a backup nobody writes to has no
    use for a write-ahead log. best-effort: a backup that stays in WAL mode
    is still a good backup."""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=DELETE").fetchone()
    except sqlite3.Error as e:
        logger.debug("backup left in its source's journal mode (%s): %s", db_path, e)
    finally:
        _close_quietly(conn)
    for side in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.remove(db_path + side)


def safe_backup(src_path: str, dst_path: str, *, verify_source: bool = True,
                verify_result: bool = True, progress=None) -> None:
    """Back up ``src_path`` to ``dst_path`` via the SQLite Online Backup API,
    refusing to produce a backup from (or keep a backup of) a corrupt DB.

    Raises ``DBIntegrityError`` and removes any partial ``dst_path`` when the
    source is unhealthy (``verify_source``) or the produced backup fails its
    own check (``verify_result``). On success ``dst_path`` is a verified-good
    copy.

    ``progress(stage, done, total)`` hears ``check``, ``copy`` with the bytes
    copied so far, and ``verify``; given one, the copy runs in steps so there
    is something to report.
    """
    report = progress or (lambda *_: None)
    if verify_source:
        report("check", 0, 0)
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
        if progress is None:
            src.backup(dst)
        else:
            page = src.execute("PRAGMA page_size").fetchone()[0]
            report("copy", 0, 0)
            src.backup(dst, pages=_BACKUP_STEP_PAGES, progress=lambda _status, remaining, total:
                       report("copy", (total - remaining) * page, total * page))
    except BaseException:
        # connect() already created dst_path; don't leave it behind empty
        _close_quietly(dst)
        dst = None
        with contextlib.suppress(OSError):
            os.remove(dst_path)
        raise
    finally:
        _close_quietly(dst)
        _close_quietly(src)

    _make_self_contained(dst_path)

    if verify_result:
        report("verify", 0, 0)
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
