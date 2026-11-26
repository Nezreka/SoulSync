"""the databases that ride along with a music backup.

a backup was the music database, and separately the video one. two more
hold things nobody can get back from a rescan, and nothing backed them up:

  player.db      what each profile watched and where it stopped, its
                 watchlist, notes, guide channels and lineups, its page
                 layouts and settings, which libraries are hidden from whom
  audiobooks.db  the audiobook library, its wishlist, listening positions

they are small, so they are backed up whenever the music database is (by
hand or on the schedule), each beside itself as ``<db>.backup_<stamp>`` with
the same verified copy and the same rotation. a companion that can't be
backed up is said in the result and never fails the music backup.

restoring one is a file swap with SoulSync stopped: there is no restore
button for these yet.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("backup.companions")


def companion_databases() -> List[Tuple[str, str]]:
    """(name, path) of each companion database that exists"""
    out: List[Tuple[str, str]] = []
    try:
        from database.player_database import default_path
        out.append(("player", str(default_path())))
    except Exception:  # noqa: BLE001
        logger.debug("no player database path", exc_info=True)
    try:
        from core.audiobook_database import resolve_db_path
        books = str(resolve_db_path())
        # its default is a path under the working folder. that is only THIS
        # install's audiobook database when the music database is there too:
        # a music database somewhere else (a test's, a second instance's) must
        # not reach back and back up the one in the working folder.
        music = os.environ.get("DATABASE_PATH", "database/music_library.db")
        beside = os.path.dirname(os.path.abspath(books)) == os.path.dirname(os.path.abspath(music))
        if os.environ.get("AUDIOBOOK_DATABASE_PATH") or beside:
            out.append(("audiobooks", books))
    except Exception:  # noqa: BLE001
        logger.debug("no audiobook database path", exc_info=True)
    return [(name, path) for name, path in out if path and os.path.exists(path)]


def backup_companions(stamp: str, max_keep: int) -> List[Dict[str, Any]]:
    """back each companion up as ``<db>.backup_<stamp>`` and rotate its old
    ones. returns [{name, ok, file | error, size_bytes}]. never raises."""
    from core.db_integrity import DBIntegrityError, room_for, rotate_backups, safe_backup
    results: List[Dict[str, Any]] = []
    for name, path in companion_databases():
        dest = f"{path}.backup_{stamp}"
        try:
            short = room_for(path, os.path.getsize(path))
            if short:
                raise OSError(short)
            safe_backup(path, dest)
            rotate_backups(path, max_keep)
            results.append({"name": name, "ok": True, "file": os.path.basename(dest),
                            "size_bytes": os.path.getsize(dest)})
        except (DBIntegrityError, OSError, Exception) as e:  # noqa: BLE001 - said, never raised
            logger.error("companion backup of %s failed: %s", name, e)
            results.append({"name": name, "ok": False, "error": str(e)})
    key = backup_key_file(stamp)
    if key.get("ok") or key.get("error") != "no key file":
        results.append(key)
    return results


# ── the key file ──────────────────────────────────────────────────────────

KEY_KEEP = 5


def key_file_path() -> str:
    """where the encryption key lives: beside the music database. every saved
    credential (service logins, api keys) is encrypted with it, so a database
    restored on a machine without it has none of them."""
    music = os.environ.get("DATABASE_PATH", "database/music_library.db")
    return os.path.join(os.path.dirname(music) or ".", ".encryption_key")


def backup_key_file(stamp: str, max_keep: int = KEY_KEEP) -> Dict[str, Any]:
    """a copy of the key beside itself (``.encryption_key.backup_<stamp>``),
    the newest ``max_keep`` kept. it guards against the key being deleted or
    damaged; a restore on ANOTHER machine needs the key downloaded with the
    backup (the Backups page offers it). never raises."""
    import re
    import shutil
    path = key_file_path()
    if not os.path.isfile(path):
        return {"name": "key", "ok": False, "error": "no key file"}
    dest = f"{path}.backup_{stamp}"
    try:
        shutil.copy2(path, dest)
        folder, base = os.path.dirname(path) or ".", os.path.basename(path)
        copies = sorted(n for n in os.listdir(folder)
                        if re.match(re.escape(base) + r"\.backup_\d{8}_\d{6}$", n))
        for old in copies[:-max_keep]:
            os.remove(os.path.join(folder, old))
        return {"name": "key", "ok": True, "file": os.path.basename(dest), "size_bytes": os.path.getsize(dest)}
    except OSError as e:
        logger.error("key file backup failed: %s", e)
        return {"name": "key", "ok": False, "error": str(e)}


# ── listing and restoring a companion ─────────────────────────────────────

LABELS = {"player": "Player", "audiobooks": "Audiobooks"}
ABOUT = {
    "player": "What each profile watched and where it stopped, watchlists, notes, guide lineups, "
              "page layouts and player settings.",
    "audiobooks": "The audiobook library, its wishlist and listening positions.",
}
PRE_RESTORE_KEEP = 3


def companions_overview() -> List[Dict[str, Any]]:
    """each companion with its backups, newest first"""
    from core.db_integrity import list_backups
    out = []
    for name, path in companion_databases():
        backups = []
        for b in sorted(list_backups(path), reverse=True):
            try:
                st = os.stat(b)
            except OSError:
                continue
            backups.append({"filename": os.path.basename(b), "size_bytes": st.st_size, "modified": st.st_mtime})
        out.append({"name": name, "label": LABELS.get(name, name), "about": ABOUT.get(name, ""),
                    "size_bytes": os.path.getsize(path), "backups": backups})
    return out


class CompanionRestoreError(Exception):
    """why a companion restore did not happen (nothing was changed)"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def restore_companion(name: str, filename: str) -> Dict[str, Any]:
    """put one companion database back from one of its backups.

    the same order as the music restore: the backup is checked first, a
    backup from a newer SoulSync is refused, a verified copy of what is there
    now is kept outside the rotation (``<db>.pre-restore_<stamp>``), and only
    then is the backup written over the live file, through sqlite's backup
    api so a reader in the middle of a query is not handed a torn file.
    raises CompanionRestoreError, with nothing changed, when it can't."""
    import sqlite3
    from datetime import datetime

    from core import db_integrity as dbi
    paths = dict(companion_databases())
    path = paths.get(str(name or ""))
    if not path:
        raise CompanionRestoreError("No such database.", 404)
    base = os.path.basename(str(filename or ""))
    wanted = {os.path.basename(b): b for b in dbi.list_backups(path)}
    source = wanted.get(base)
    if not source:
        raise CompanionRestoreError("No such backup.", 404)
    if not dbi.is_healthy(source):
        raise CompanionRestoreError(
            "That backup failed its integrity check, so it was not restored. Nothing was changed.", 409)
    theirs, ours = dbi.schema_version(source), dbi.schema_version(path)
    if theirs is not None and ours is not None and theirs > ours:
        raise CompanionRestoreError(
            "That backup is from a newer SoulSync. Update SoulSync first, then restore it. "
            "Nothing was changed.", 409)
    short = dbi.room_for(path, os.path.getsize(path))
    if short:
        raise CompanionRestoreError(short + " Nothing was changed.", 507)
    if not dbi.backup_lock.acquire(blocking=False):
        raise CompanionRestoreError("Another backup or restore is running. Try again when it finishes.", 409)
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety = f"{path}.pre-restore_{stamp}"
        try:
            dbi.safe_backup(path, safety, verify_source=False)
        except Exception as e:  # noqa: BLE001 - no way back, no restore
            raise CompanionRestoreError(
                f"Could not keep a copy of the current database first, so nothing was restored. ({e})", 500) from e
        _prune_pre_restore(path)
        src, dst = sqlite3.connect(source), sqlite3.connect(path, timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    finally:
        dbi.backup_lock.release()
    logger.warning("%s database restored from %s (previous kept as %s)", name, base, os.path.basename(safety))
    return {"name": name, "restored_from": base, "safety_copy": os.path.basename(safety)}


def _prune_pre_restore(path: str) -> None:
    import re
    from core.db_integrity import remove_backup
    folder, base = os.path.dirname(path) or ".", os.path.basename(path)
    try:
        names = sorted(n for n in os.listdir(folder)
                       if re.match(re.escape(base) + r"\.pre-restore_\d{8}_\d{6}$", n))
    except OSError:
        return
    for old in names[:-PRE_RESTORE_KEEP]:
        remove_backup(os.path.join(folder, old))


__all__ = ["companion_databases", "backup_companions", "backup_key_file", "key_file_path",
           "companions_overview", "restore_companion", "CompanionRestoreError"]
