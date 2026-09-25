"""Whose library a file belongs to follows from where it is (#1199, E-15).

Every library is a folder: the shared transfer folder, and one output folder
per profile that keeps a library of its own. So the owner of a file is not a
fact to be carried from the download that produced it through five worker
threads and stamped at the end -- it is a function of the file's path, and it
can be recomputed at any time.

That is what this module keeps true. ``lib2_library_roots`` holds one row per
library folder (``profile_id`` 0 = the shared library), and two triggers on
``lib2_track_files`` set ``owner_profile_id`` from the LONGEST matching prefix
whenever a row is inserted or its path changes. An own folder inside the shared
one is therefore that profile's, and a file a reorganize moves between two
libraries changes hands with the move.

Why triggers and not a helper at each writer: some twenty places write
``lib2_track_files.path`` (imports, the atomic publish, reorganize, the repair
fixes, path-drift reconcile, the importer), and upstream adds new ones every
release. A rule enforced by the table cannot be forgotten by the next writer.

Why pure SQL: a trigger that called a Python function would make every write
fail on a connection that did not register it (tests, tools, a raw sqlite3).

A path under no known folder keeps whatever its writer stamped. That is the
legacy import's owner, or a media-server path this process cannot map.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.library_roots")

#: ``profile_id`` for the shared library's folder. Not NULL: SQLite's
#: ``NULLIF`` turns it back into the NULL the catalogue means by "shared", while
#: a real NULL here could not be told apart from "no folder matched".
SHARED = 0

LIBRARY_ROOTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_library_roots (
    prefix TEXT PRIMARY KEY,                 -- absolute folder, with a trailing separator
    profile_id INTEGER NOT NULL              -- 0 = the shared library
)
"""

# The longest folder that contains `path`, as the owner the catalogue stores.
_OWNER_OF = (
    "(SELECT NULLIF(r.profile_id, 0) FROM lib2_library_roots r"
    " WHERE substr({path}, 1, length(r.prefix)) = r.prefix"
    " ORDER BY length(r.prefix) DESC LIMIT 1)"
)
_UNDER_ANY = (
    "EXISTS (SELECT 1 FROM lib2_library_roots r"
    " WHERE substr({path}, 1, length(r.prefix)) = r.prefix)"
)

_TRIGGER_NAMES = ("trg_lib2_file_owner_by_path_insert", "trg_lib2_file_owner_by_path_move")


def _trigger_sql() -> List[str]:
    owner = _OWNER_OF.format(path="NEW.path")
    under = _UNDER_ANY.format(path="NEW.path")
    return [
        f"""
        CREATE TRIGGER {_TRIGGER_NAMES[0]}
        AFTER INSERT ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.path IS NOT NULL AND {under}
        BEGIN
            UPDATE lib2_track_files SET owner_profile_id = {owner} WHERE id = NEW.id;
        END
        """,
        f"""
        CREATE TRIGGER {_TRIGGER_NAMES[1]}
        AFTER UPDATE OF path ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.path IS NOT NULL AND NEW.path IS NOT OLD.path AND {under}
        BEGIN
            UPDATE lib2_track_files SET owner_profile_id = {owner} WHERE id = NEW.id;
        END
        """,
    ]


def ensure_library_roots_schema(cursor: Any) -> None:
    """The folder table and its two triggers. Idempotent; the triggers are
    rebuilt every start so a change to their body reaches old installs."""
    cursor.execute(LIBRARY_ROOTS_DDL)
    for name in _TRIGGER_NAMES:
        cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
    for sql in _trigger_sql():
        cursor.execute(sql)


def normalize_prefix(folder: Any) -> str:
    """The prefix form of a folder: absolute, normalised, one trailing '/'.
    The separator is what stops ``/music/kim`` from claiming ``/music/kimberly``."""
    text = str(folder or "").strip()
    if not text:
        return ""
    return os.path.normpath(text).rstrip("/\\") + "/"


def desired_roots(shared_root: Optional[str],
                  own_roots: Iterable[Tuple[int, Optional[str]]]) -> Dict[str, int]:
    """``{prefix: profile_id}`` for the shared folder and every own folder.
    An own folder that equals the shared one is refused at save time; should
    one slip through, the own library wins it, as the longer claim would."""
    roots: Dict[str, int] = {}
    shared = normalize_prefix(shared_root)
    if shared:
        roots[shared] = SHARED
    for profile_id, root in own_roots:
        prefix = normalize_prefix(root)
        if prefix and profile_id:
            roots[prefix] = int(profile_id)
    return roots


def current_roots(cursor: Any) -> Dict[str, int]:
    try:
        return {str(p): int(pid) for p, pid in cursor.execute(
            "SELECT prefix, profile_id FROM lib2_library_roots").fetchall()}
    except Exception:  # noqa: BLE001 - table not there yet: no folders known
        return {}


def apply_roots(cursor: Any, roots: Dict[str, int]) -> int:
    """Make the folder table say ``roots`` and re-derive every file under one.

    Returns how many file rows changed hands. A no-op, and no full-table
    update, when the folders are unchanged -- this runs on every start.
    """
    if current_roots(cursor) == roots:
        return 0
    cursor.execute("DELETE FROM lib2_library_roots")
    cursor.executemany(
        "INSERT INTO lib2_library_roots(prefix, profile_id) VALUES(?, ?)",
        sorted(roots.items()))
    owner = _OWNER_OF.format(path="lib2_track_files.path")
    under = _UNDER_ANY.format(path="lib2_track_files.path")
    cursor.execute(
        f"UPDATE lib2_track_files SET owner_profile_id = {owner}"
        f" WHERE path IS NOT NULL AND {under}"
        f"   AND owner_profile_id IS NOT {owner}")
    moved = max(int(cursor.rowcount or 0), 0)
    if moved:
        logger.info("library folders changed: %d file row(s) re-assigned to the library "
                    "whose folder holds them", moved)
    return moved


def owner_for_path(roots: Dict[str, int], path: Any) -> Tuple[bool, Optional[int]]:
    """``(known, owner)`` for one path, in Python, with the table's rule:
    the longest folder that contains it. ``known`` is False when no folder
    does. For readers that have a path but no file row (repair findings)."""
    text = str(path or "")
    best: Optional[str] = None
    for prefix in roots:
        if text.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None:
        return False, None
    pid = roots[best]
    return True, (None if pid == SHARED else pid)


def configured_roots(database: Any = None) -> Dict[str, int]:
    """The folders as configured right now: the shared transfer folder, plus the
    own folder of every profile that keeps a library of its own -- when the
    active media server supports own libraries at all. On one that does not,
    every profile reads the shared library (``library_scope_for_profile``), so
    an own folder claiming files would hide them from everyone."""
    from core.imports.paths import library_root_for_profile, shared_transfer_root

    if database is None:
        from database.music_database import get_database
        database = get_database()
    own: List[Tuple[int, Optional[str]]] = []
    try:
        from core.library_scope import own_library_supported
        if own_library_supported():
            for profile in database.get_own_library_profiles() or []:
                own.append((int(profile["id"]),
                            library_root_for_profile(profile["id"], announce=False)))
    except Exception as exc:  # noqa: BLE001 - no profiles readable: shared only
        logger.debug("own library folders unavailable: %s", exc)
    try:
        shared = shared_transfer_root()
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared transfer folder unavailable: %s", exc)
        shared = None
    return desired_roots(shared, own)


def sync_library_roots(database: Any = None) -> int:
    """Bring the folder table in line with the configuration and re-derive the
    owners. Called at start, and whenever a profile's library, the transfer
    folder or the active media server changes. Never raises."""
    try:
        if database is None:
            from database.music_database import get_database
            database = get_database()
        roots = configured_roots(database)
        conn = database._get_connection()
        try:
            cursor = conn.cursor()
            ensure_library_roots_schema(cursor)
            moved = apply_roots(cursor, roots)
            conn.commit()
            return moved
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - ownership falls back to the writers' stamps
        logger.error("could not sync library folders: %s", exc)
        return 0


def load_roots(conn: Any) -> Dict[str, int]:
    """The folder table as a dict, for ``owner_for_path``."""
    return current_roots(conn.cursor() if hasattr(conn, "cursor") else conn)


__all__ = [
    "SHARED", "apply_roots", "configured_roots", "current_roots", "desired_roots",
    "ensure_library_roots_schema", "load_roots", "normalize_prefix",
    "owner_for_path", "sync_library_roots",
]
