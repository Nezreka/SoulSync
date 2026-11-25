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
    return results


__all__ = ["companion_databases", "backup_companions"]
