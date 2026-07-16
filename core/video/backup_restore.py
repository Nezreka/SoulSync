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
    out = []
    for p in glob.glob(_db_path() + ".backup_*"):
        if p.endswith(("-wal", "-shm")):
            continue
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
    p = os.path.join(os.path.dirname(_db_path()) or ".", base)
    return p if os.path.exists(p) else None


def create_now() -> Dict[str, Any]:
    """One on-demand backup — same verified snapshot + pruning the scheduled
    automation makes."""
    from core.db_integrity import DBIntegrityError, prune_backups, safe_backup
    db_path = _db_path()
    if not os.path.exists(db_path):
        return {"ok": False, "error": "Database file not found."}
    dest = "%s.backup_%s" % (db_path, datetime.now().strftime("%Y%m%d_%H%M%S"))
    try:
        safe_backup(db_path, dest)
    except DBIntegrityError as e:
        return {"ok": False, "error": "Refused — the database failed its integrity check: %s" % e}
    for removed in prune_backups(list(glob.glob(db_path + ".backup_*")), MAX_BACKUPS):
        try:
            os.remove(removed)
        except OSError:
            logger.debug("backup prune failed for %s", removed, exc_info=True)
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
    pending = _pending_path()
    try:
        with sqlite3.connect(src) as s, sqlite3.connect(pending) as d:
            s.backup(d)
    except sqlite3.Error as e:
        try:
            os.remove(pending)
        except OSError:
            pass
        return {"ok": False, "error": "Could not stage the backup: %s" % e}
    logger.warning("video restore STAGED from %s — applies on next restart", os.path.basename(src))
    return {"ok": True, "pending": os.path.basename(src)}


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
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.exists(db_path):
            keep = "%s.pre-restore-%s" % (db_path, ts)
            shutil.move(db_path, keep)
            logger.warning("video restore: current database set aside as %s", os.path.basename(keep))
        # the OLD database's WAL/SHM must leave with it — sqlite would replay a
        # stale WAL against the restored file and corrupt it
        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.exists(side):
                shutil.move(side, "%s.pre-restore-%s%s" % (db_path, ts, suffix))
        shutil.move(pending, db_path)
        logger.warning("video restore APPLIED — database swapped in from the staged backup")
        return True
    except OSError:
        logger.exception("video restore failed — the staged file is left in place")
        return False
