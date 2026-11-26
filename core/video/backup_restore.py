"""Video DB backups: browse / create / RESTORE (arr-parity P10).

Scheduled hot backups already exist (the 'video_backup_database' automation:
integrity-verified sqlite snapshots named ``<db>.backup_<ts>`` with rolling
pruning). What was missing vs Radarr is everything around them: seeing what
you have, making one on demand, and — the part that matters at 3am — getting
one BACK.

Restore is staged, never hot: a running app holds open connections, so the
picked backup is verified (PRAGMA quick_check) and copied to
``<db>.restore-pending``; the swap happens at the NEXT startup, before any
connection opens. The current database is never deleted — it's set aside as
``<db>.pre-restore-<ts>`` so even a restore can be undone. That preserves the
house rule: nothing here ever destroys live data.
"""

from __future__ import annotations

import glob
import re
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.backup_restore")

MAX_BACKUPS = 8


def _db_path() -> str:
    return os.environ.get("VIDEO_DATABASE_PATH", "database/video_library.db")


def _pending_path(db_path: Optional[str] = None) -> str:
    return (db_path or _db_path()) + ".restore-pending"


def list_backups() -> List[Dict[str, Any]]:
    """Available backups, newest first: [{name, size_bytes, created_at}].
    WAL/SHM sidecars are part of their backup, not backups themselves."""
    from core.db_integrity import list_backups as _backup_files
    out = []
    for p in _backup_files(_db_path()):
        try:
            st = os.stat(p)
            out.append({"name": os.path.basename(p), "size_bytes": st.st_size,
                        "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        except OSError:
            continue
    out.sort(key=lambda b: b["name"], reverse=True)
    return out


def _resolve(name: str) -> Optional[str]:
    """A backup NAME (never a path — traversal-proof) → its full path."""
    base = os.path.basename(str(name or ""))
    if not base.startswith(os.path.basename(_db_path()) + ".backup_"):
        return None
    # a backup, not one of the files beside it (-wal, -shm, .meta.json)
    if not re.search(r"\.backup_\d{8}_\d{6}$", base):
        return None
    p = os.path.join(os.path.dirname(_db_path()) or ".", base)
    return p if os.path.exists(p) else None


def create_now() -> Dict[str, Any]:
    """One on-demand backup — same verified snapshot + pruning the scheduled
    automation makes."""
    from core.db_integrity import DBIntegrityError, backup_lock, room_for, rotate_backups, safe_backup
    db_path = _db_path()
    if not os.path.exists(db_path):
        return {"ok": False, "error": "Database file not found."}
    dest = "%s.backup_%s" % (db_path, datetime.now().strftime("%Y%m%d_%H%M%S"))
    short = room_for(db_path, os.path.getsize(db_path))
    if short:
        return {"ok": False, "error": short}
    if not backup_lock.acquire(blocking=False):
        return {"ok": False, "error": "Another backup or restore is running. Try again when it finishes."}
    try:
        safe_backup(db_path, dest)
    except DBIntegrityError as e:
        return {"ok": False, "error": "Refused — the database failed its integrity check: %s" % e}
    finally:
        backup_lock.release()
    # counted in backups, not in the -wal / -shm files beside them
    rotate_backups(db_path, MAX_BACKUPS)
    return {"ok": True, "name": os.path.basename(dest),
            "size_bytes": os.path.getsize(dest)}


def stage_restore(name: str) -> Dict[str, Any]:
    """Verify a backup and stage it for the next startup. The staged file is
    produced through sqlite's backup API, so it's SELF-CONTAINED no matter
    what WAL state the source backup carries — a bare file copy could lose
    everything still sitting in the backup's -wal sidecar."""
    import sqlite3
    from core.db_integrity import is_healthy
    src = _resolve(name)
    if not src or src.endswith(("-wal", "-shm")):
        return {"ok": False, "error": "Unknown backup."}
    if not is_healthy(src):
        return {"ok": False, "error": "That backup failed its integrity check — not staging it."}
    newer = _newer_schema(src)
    if newer:
        return {"ok": False, "error": newer}
    from core.db_integrity import room_for
    short = room_for(_db_path(), os.path.getsize(src))
    if short:
        return {"ok": False, "error": short}
    pending = _pending_path()
    # staged under another name and only renamed into place once it is whole
    # and checked: startup swaps in whatever is called .restore-pending, and
    # a restart during the copy (the page says "restart to apply") swapped in
    # half a database
    partial = pending + ".tmp"
    try:
        s, d = sqlite3.connect(src), sqlite3.connect(partial)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        if not is_healthy(partial):
            raise sqlite3.DatabaseError("the staged copy failed its integrity check")
        os.replace(partial, pending)
    except (sqlite3.Error, OSError) as e:
        for leftover in (partial, partial + "-wal", partial + "-shm"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        return {"ok": False, "error": "Could not stage the backup: %s" % e}
    logger.warning("video restore STAGED from %s — applies on next restart", os.path.basename(src))
    return {"ok": True, "pending": os.path.basename(src)}


def _newer_schema(backup_path: str) -> Optional[str]:
    """a sentence when the backup was made by a newer SoulSync than this one"""
    try:
        from core.db_integrity import schema_version
        from database.video_database import SCHEMA_VERSION
        theirs = schema_version(backup_path)
    except Exception:  # noqa: BLE001 - can't tell is no reason to refuse
        return None
    if theirs is None or theirs <= int(SCHEMA_VERSION):
        return None
    return ("That backup is from a newer SoulSync (video database version %d; this one runs %d). "
            "Update SoulSync first, then restore it." % (theirs, int(SCHEMA_VERSION)))


def pending_restore() -> bool:
    return os.path.exists(_pending_path())


def cancel_restore() -> bool:
    p = _pending_path()
    if not os.path.exists(p):
        return False
    os.remove(p)
    return True


def apply_pending_restore(db_path: str) -> bool:
    """Called at database startup BEFORE any connection opens. If a staged
    restore exists: set the current DB aside (``.pre-restore-<ts>`` — kept,
    never deleted) and move the staged file into place."""
    pending = _pending_path(db_path)
    if not os.path.exists(pending):
        return False
    from core.db_integrity import is_healthy
    # a staged file that isn't a whole database is never swapped in
    if not is_healthy(pending):
        bad = pending + ".bad"
        try:
            os.replace(pending, bad)
        except OSError:
            pass
        logger.error("video restore NOT applied: the staged file failed its integrity check "
                     "(kept as %s). the current database is untouched.", os.path.basename(bad))
        return False
    moved = []      # (from, to) of everything set aside, to put back if the swap fails
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.exists(db_path):
            keep = "%s.pre-restore-%s" % (db_path, ts)
            shutil.move(db_path, keep)
            moved.append((db_path, keep))
            logger.warning("video restore: current database set aside as %s", os.path.basename(keep))
        # the OLD database's WAL/SHM must leave with it — sqlite would replay a
        # stale WAL against the restored file and corrupt it
        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.exists(side):
                kept = "%s.pre-restore-%s%s" % (db_path, ts, suffix)
                shutil.move(side, kept)
                moved.append((side, kept))
        shutil.move(pending, db_path)
        logger.warning("video restore APPLIED — database swapped in from the staged backup")
        return True
    except OSError:
        # the current database was already set aside: put it back, or startup
        # would go on to make an EMPTY one and run against that
        for original, kept in reversed(moved):
            try:
                if not os.path.exists(original):
                    shutil.move(kept, original)
            except OSError:
                logger.exception("video restore: could not put %s back", os.path.basename(original))
        logger.exception("video restore failed — the database was put back and the staged file is left in place")
        return False
