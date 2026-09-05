"""ADR-05 file-removal journal and physical-delete safety boundary.

Physical deletion is deliberately separate from removing a Library-v2 entity.
This module first materializes the DB scope, closes SQLite, then resolves and
stats files. A file is deletable only when its real path is contained by an
explicitly configured ``library.music_paths`` root; unknown mounts, symlink
escapes and paths outside those roots fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional


FILE_DELETE_OPERATIONS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_file_delete_operations (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    preview_token TEXT NOT NULL,
    status TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    total_size INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'permanent',
    actor TEXT NOT NULL DEFAULT 'user',
    actor_profile_id INTEGER,
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
)
"""
FILE_DELETE_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_file_delete_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    file_ids_json TEXT NOT NULL,
    stored_paths_json TEXT NOT NULL,
    resolved_path TEXT NOT NULL,
    root_path TEXT NOT NULL,
    size INTEGER,
    mtime_ns INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    deleted_at TIMESTAMP,
    FOREIGN KEY (operation_id) REFERENCES lib2_file_delete_operations(id) ON DELETE RESTRICT
)
"""


class FileDeleteError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def ensure_file_delete_schema(cursor) -> None:
    """Create the durable ADR-05 operation/item journal."""
    cursor.execute(FILE_DELETE_OPERATIONS_DDL)
    cursor.execute(FILE_DELETE_ITEMS_DDL)
    operation_columns = {
        str(row[1]) for row in cursor.execute(
            "PRAGMA table_info(lib2_file_delete_operations)"
        ).fetchall()
    }
    for column, ddl in (
        ("mode", "TEXT NOT NULL DEFAULT 'permanent'"),
        ("actor", "TEXT NOT NULL DEFAULT 'user'"),
        ("actor_profile_id", "INTEGER"),
    ):
        if column not in operation_columns:
            cursor.execute(
                f"ALTER TABLE lib2_file_delete_operations ADD COLUMN {column} {ddl}"
            )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_file_delete_items_operation "
        "ON lib2_file_delete_items(operation_id, status)"
    )


def _library_roots(config_manager: Any = None, *, include_import_root: bool = True) -> List[str]:
    """The folders a library file may legitimately live in.

    ``library.music_paths`` is what the user declared — and it is optional,
    defaults to ``[]``, and plenty of installs never fill it. Taking it as the
    only answer made permanent deletion impossible on a default setup: every
    file previewed as ``outside_configured_library_roots`` and the error named
    no setting to fix. So the organize destination
    (``soulseek.transfer_path``) counts too: that is the folder SoulSync's own
    import pipeline files the library into, which makes it a library root by
    construction rather than by configuration.

    The incoming download folder is deliberately NOT a root. Those files belong
    to the downloader until the import moves them; the library's delete command
    has no business there.

    ``include_import_root=False`` is for :func:`fuzzy_resolved_path_is_deletable`,
    where the transfer folder is precisely the hazard (iss29-E04): a resolver
    GUESS landing on a freshly imported file must never be deleted.
    """
    def _read(key):
        try:
            return config_manager.get(key, None)
        except Exception:  # noqa: BLE001
            return None

    if config_manager is None:
        try:
            from core.settings import config_manager as _config_manager
            config_manager = _config_manager
        except Exception:  # noqa: BLE001
            return []

    configured = _read("library.music_paths") or []
    if isinstance(configured, str):
        configured = [configured]
    candidates = list(configured)
    if include_import_root:
        import_root = _read("soulseek.transfer_path")
        if isinstance(import_root, str) and import_root.strip():
            candidates.append(import_root)

    from core.imports.paths import docker_resolve_path

    roots: List[str] = []
    for raw in candidates:
        if not isinstance(raw, str) or not raw.strip():
            continue
        resolved = os.path.realpath(
            os.path.abspath(os.path.expanduser(docker_resolve_path(raw.strip())))
        )
        if os.path.isdir(resolved) and resolved not in roots:
            roots.append(resolved)
    return roots


def _containing_root(path: str, roots: List[str]) -> Optional[str]:
    """Return the deepest configured root containing ``path``; fail closed."""
    real_path = os.path.realpath(path)
    matches = []
    for root in roots:
        try:
            if os.path.commonpath((root, real_path)) == root and real_path != root:
                matches.append(root)
        except (OSError, ValueError):
            continue
    return max(matches, key=len) if matches else None


def fuzzy_resolved_path_is_deletable(
    original: str, resolved: str, config_manager: Any = None,
) -> bool:
    """May a destructive repair delete ``resolved``, given it came from
    ``original`` via the fuzzy path resolver?

    iss29-E04. ``resolve_library_file_path`` suffix-walks the configured base
    directories to recover a moved file, and it tries the **transfer folder
    first** (``core/library/path_resolver.py``). Imports land under
    ``soulseek.transfer_path`` in the very same ``Artist/Album/…`` layout, so a
    finding on a library file that has since vanished can resolve onto a freshly
    downloaded replacement — and the destructive fixes then deleted the download
    and recorded the finding as converged.

    The rule is only applied when the resolver actually guessed: a path that
    exists exactly as the catalogue records it is the file the user asked about,
    and is deleted without further ceremony. A GUESSED path must land inside a
    configured library root — the same containment ADR-05 already enforces for
    the Library V2 delete pipeline (:func:`_containing_root`).

    Fails closed: with no usable roots configured there is nothing to validate a
    guess against, so the guess is not acted on.
    """
    if not resolved:
        return False
    try:
        if os.path.realpath(resolved) == os.path.realpath(original):
            return True  # not a guess — the catalogue path itself
    except OSError:
        pass
    # Music paths only: the transfer folder is the hazard this guard exists
    # for, not a safe harbour (see ``_library_roots``).
    roots = _library_roots(config_manager, include_import_root=False)
    if not roots:
        return False
    return _containing_root(resolved, roots) is not None


ALREADY_GONE = "already_gone"


def _absent_reason(path: Any, config_manager: Any = None) -> str:
    """Why a path could not be stat'ed — gone, or merely out of reach.

    dd28-19's lesson, applied to the preview: absence is only credible when
    the storage that should hold the file is reachable. An unmounted share
    makes every one of its files look deleted, and retiring those rows would
    "delete" a library that is alive on a disk we simply cannot see.
    """
    from core.library2.paths import missing_path_root_is_healthy

    text = str(path or "")
    if text and missing_path_root_is_healthy(text, config_manager):
        return ALREADY_GONE
    return "path_unresolved"


def _scope_snapshot(
    database, entity: str, entity_id: int, file_ids: Optional[List[int]] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """Read the exact owned-file scope and close SQLite before path I/O.

    ``file_ids``, when given, narrows the normal whole-entity scope to a
    caller-selected subset (C2: Manage Track Files bulk-delete) — the SQL
    filter is still bounded by the entity's own ownership, so a stray id
    outside this artist/album is silently dropped rather than trusted.
    """
    if entity not in ("artists", "albums"):
        raise FileDeleteError("Unsupported entity")
    id_filter, id_params = "", []
    if file_ids is not None:
        if not file_ids:
            raise FileDeleteError("file_ids must not be empty")
        marks = ",".join("?" for _ in file_ids)
        id_filter = f" AND tf.id IN ({marks})"
        id_params = [int(f) for f in file_ids]
    conn = database._get_connection()
    try:
        if entity == "artists":
            from core.library2.artist_aliases import resolve_alias_group

            entity_row = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (int(entity_id),)
            ).fetchone()
            if not entity_row:
                raise FileDeleteError("Artist not found", 404)
            artist_ids = resolve_alias_group(conn, entity_id)
            artist_marks = ",".join("?" for _ in artist_ids)
            rows = conn.execute(
                f"""SELECT tf.id AS file_id, tf.track_id, tf.path AS stored_path,
                          tf.size AS db_size, tf.file_state, t.title AS track_title,
                          al.id AS album_id, al.title AS album_title
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                     JOIN lib2_albums al ON al.id=t.album_id
                    WHERE al.primary_artist_id IN ({artist_marks})
                      AND tf.file_state<>'deleted'{id_filter}
                    ORDER BY al.id, t.id, tf.id""",
                (*artist_ids, *id_params),
            ).fetchall()
            title = entity_row["name"]
        else:
            entity_row = conn.execute(
                "SELECT title FROM lib2_albums WHERE id=?", (int(entity_id),)
            ).fetchone()
            if not entity_row:
                raise FileDeleteError("Album not found", 404)
            rows = conn.execute(
                f"""SELECT tf.id AS file_id, tf.track_id, tf.path AS stored_path,
                          tf.size AS db_size, tf.file_state, t.title AS track_title,
                          al.id AS album_id, al.title AS album_title
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                     JOIN lib2_albums al ON al.id=t.album_id
                    WHERE al.id=? AND tf.file_state<>'deleted'{id_filter}
                    ORDER BY t.id, tf.id""",
                (int(entity_id), *id_params),
            ).fetchall()
            title = entity_row["title"]
        return str(title), [dict(row) for row in rows]
    finally:
        conn.close()


def preview_entity_files(
    database,
    *,
    entity: str,
    entity_id: int,
    file_ids: Optional[List[int]] = None,
    config_manager: Any = None,
) -> Dict[str, Any]:
    """Build a deterministic, non-mutating physical-delete preview.

    ``file_ids`` narrows the scope to a caller-selected subset of this
    entity's files (C2) — everything else (root-safety, journaling, the
    preview-token contract) is unchanged.
    """
    from core.library2.paths import resolve_lib2_path

    title, rows = _scope_snapshot(database, entity, entity_id, file_ids)
    roots = _library_roots(config_manager)
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        resolved = resolve_lib2_path(row["stored_path"], config_manager=config_manager)
        real_path = os.path.realpath(resolved) if resolved else None
        key = real_path or f"unresolved:{row['file_id']}"
        item = grouped.setdefault(
            key,
            {
                "file_ids": [],
                "track_ids": [],
                "stored_paths": [],
                "path": real_path,
                "root": None,
                "size": int(row["db_size"] or 0) or None,
                "mtime_ns": None,
                "deletable": False,
                "reason": _absent_reason(
                    resolved or row["stored_path"], config_manager),
                "album_id": row["album_id"],
                "album_title": row["album_title"],
                "track_titles": [],
            },
        )
        item["file_ids"].append(int(row["file_id"]))
        item["track_ids"].append(int(row["track_id"]))
        item["stored_paths"].append(row["stored_path"])
        item["track_titles"].append(row["track_title"])
        if row["db_size"]:
            item["size"] = max(int(item["size"] or 0), int(row["db_size"]))
        if real_path:
            root = _containing_root(real_path, roots)
            item["root"] = root
            if not root:
                item["reason"] = "outside_configured_library_roots"
            elif not os.path.exists(real_path):
                item["reason"] = _absent_reason(real_path, config_manager)
            elif not os.path.isfile(real_path):
                item["reason"] = "not_a_regular_file"
            elif not fuzzy_resolved_path_is_deletable(
                    row["stored_path"], real_path, config_manager):
                # The resolver GUESSED this path. It suffix-walks the transfer
                # folder first, and falls back to matching a sibling album
                # folder or a synthesized filename -- so a stored path that no
                # longer exists (share remounted, folder renamed) can resolve
                # onto a freshly downloaded replacement awaiting import, and
                # confirming the dialog would unlink that instead of the
                # library file. The maintenance path already applies this rule;
                # the user-facing ADR-05 dialog did not.
                item["reason"] = "resolved_path_is_a_guess"
            else:
                try:
                    stat = os.stat(real_path)
                    item.update(
                        size=int(stat.st_size),
                        mtime_ns=int(stat.st_mtime_ns),
                        deletable=True,
                        reason=None,
                    )
                except OSError:
                    item["reason"] = "stat_failed"

    files = list(grouped.values())
    token_payload = {
        "entity": entity,
        "entity_id": int(entity_id),
        "files": [
            {
                key: item[key]
                for key in (
                    "file_ids", "path", "root", "size", "mtime_ns", "deletable", "reason"
                )
            }
            for item in files
        ],
    }
    preview_token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "entity": entity,
        "entity_id": int(entity_id),
        "title": title,
        "configured_roots": roots,
        "files": files,
        "file_count": len(files),
        "deletable_count": sum(1 for item in files if item["deletable"]),
        # "Unsafe" means: this path is real and lies outside your library, so
        # deleting it could destroy something that is not the library's to
        # delete. A file that is simply GONE is not unsafe — there is nothing
        # to unlink, only a row to retire — and counting it as unsafe let one
        # already-deleted file veto the deletion of every other file on the
        # album, permanently.
        "unsafe_count": sum(
            1 for item in files
            if not item["deletable"] and item["reason"] != ALREADY_GONE
        ),
        "missing_count": sum(1 for item in files if item["reason"] == ALREADY_GONE),
        "total_size": sum(int(item["size"] or 0) for item in files),
        "preview_token": preview_token,
    }


def _operation_snapshot(conn, operation_id: str) -> Dict[str, Any]:
    operation = conn.execute(
        "SELECT * FROM lib2_file_delete_operations WHERE id=?", (operation_id,)
    ).fetchone()
    if not operation:
        raise FileDeleteError("File-delete operation not found", 404)
    items = conn.execute(
        "SELECT * FROM lib2_file_delete_items WHERE operation_id=? ORDER BY id",
        (operation_id,),
    ).fetchall()
    return {
        **dict(operation),
        "items": [
            {
                **dict(item),
                "file_ids": json.loads(item["file_ids_json"]),
                "stored_paths": json.loads(item["stored_paths_json"]),
            }
            for item in items
        ],
    }


def get_delete_operation(database, operation_id: str) -> Dict[str, Any]:
    conn = database._get_connection()
    try:
        return _operation_snapshot(conn, operation_id)
    finally:
        conn.close()


def _mark_file_rows_deleted(conn, file_ids: List[int]) -> None:
    from core.library2.track_files import set_file_state

    for file_id in file_ids:
        set_file_state(conn, int(file_id), "deleted")


def remove_entity_file_records(
    database,
    *,
    entity: str,
    entity_id: int,
    file_ids: Optional[List[int]] = None,
    actor: str = "user",
    actor_profile_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Remove file links from Library v2 while keeping files on disk.

    Rows are retained with ``file_state='deleted'`` so the operation remains
    auditable and primary-file promotion still runs through the shared file
    lifecycle helper.  This is the database-only half of §52.11's unified
    choice; it performs no path resolution and never touches the filesystem.
    """
    title, rows = _scope_snapshot(database, entity, entity_id, file_ids)
    if not rows:
        raise FileDeleteError("No library file records to remove", 409)

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row["stored_path"] or f"file:{row['file_id']}")
        item = grouped.setdefault(
            key,
            {
                "file_ids": [],
                "track_ids": [],
                "stored_paths": [],
                "path": str(row["stored_path"] or ""),
                "size": int(row["db_size"] or 0),
            },
        )
        item["file_ids"].append(int(row["file_id"]))
        item["track_ids"].append(int(row["track_id"]))
        item["stored_paths"].append(str(row["stored_path"] or ""))
        item["size"] = max(item["size"], int(row["db_size"] or 0))

    operation_id = uuid.uuid4().hex
    token_payload = {
        "entity": entity,
        "entity_id": int(entity_id),
        "file_ids": sorted(int(row["file_id"]) for row in rows),
        "mode": "database_only",
    }
    token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    conn = database._get_connection()
    try:
        ensure_file_delete_schema(conn.cursor())
        conn.execute(
            """INSERT INTO lib2_file_delete_operations(
                   id, entity_type, entity_id, preview_token, status,
                   file_count, total_size, mode, actor, actor_profile_id,
                   completed_at)
               VALUES(?,?,?,?, 'completed', ?,?, 'database_only', ?,?,
                      CURRENT_TIMESTAMP)""",
            (
                operation_id,
                entity,
                int(entity_id),
                token,
                len(grouped),
                sum(item["size"] for item in grouped.values()),
                str(actor or "user"),
                actor_profile_id,
            ),
        )
        for item in grouped.values():
            conn.execute(
                """INSERT INTO lib2_file_delete_items(
                       operation_id, file_ids_json, stored_paths_json,
                       resolved_path, root_path, size, mtime_ns, status,
                       deleted_at)
                   VALUES(?,?,?,?,?,?,NULL, 'removed', CURRENT_TIMESTAMP)""",
                (
                    operation_id,
                    json.dumps(item["file_ids"]),
                    json.dumps(item["stored_paths"]),
                    item["path"],
                    "",
                    item["size"],
                ),
            )
            _mark_file_rows_deleted(conn, item["file_ids"])
        conn.commit()
        result = _operation_snapshot(conn, operation_id)
        result["title"] = title
        result["track_ids"] = sorted({
            track_id for item in grouped.values() for track_id in item["track_ids"]
        })
        return result
    finally:
        conn.close()


def _finish_operation(conn, operation_id: str) -> None:
    counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            """SELECT status, COUNT(*) AS count
                 FROM lib2_file_delete_items WHERE operation_id=? GROUP BY status""",
            (operation_id,),
        )
    }
    pending = sum(
        counts.get(status, 0) for status in ("planned", "deleting")
    )
    failed = counts.get("failed", 0)
    status = "executing" if pending else ("partial" if failed else "completed")
    conn.execute(
        """UPDATE lib2_file_delete_operations
               SET status=?,
                   completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
             WHERE id=?""",
        (status, int(not pending), operation_id),
    )


def reconcile_incomplete_deletes(database) -> int:
    """Recover items left ``deleting`` by a process crash.

    The state is persisted immediately before unlink. If the path is now gone,
    finish the DB lifecycle; if it still exists, fail closed and require a new
    preview/command instead of deleting automatically after restart.
    """
    read_conn = database._get_connection()
    try:
        rows = [dict(row) for row in read_conn.execute(
            """SELECT id, operation_id, file_ids_json, resolved_path
                 FROM lib2_file_delete_items WHERE status='deleting'"""
        ).fetchall()]
    finally:
        read_conn.close()

    observations = [(row, os.path.exists(row["resolved_path"])) for row in rows]
    conn = database._get_connection()
    try:
        operation_ids = {row["operation_id"] for row in rows}
        recovered = 0
        for row, still_exists in observations:
            if still_exists:
                conn.execute(
                    """UPDATE lib2_file_delete_items
                          SET status='failed', error='interrupted_before_delete'
                        WHERE id=?""",
                    (row["id"],),
                )
                continue
            _mark_file_rows_deleted(conn, json.loads(row["file_ids_json"]))
            conn.execute(
                """UPDATE lib2_file_delete_items
                      SET status='deleted', error=NULL, deleted_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (row["id"],),
            )
            recovered += 1
        for operation_id in operation_ids:
            _finish_operation(conn, operation_id)
        conn.commit()
        return recovered
    finally:
        conn.close()


def _execute_delete_items(
    database,
    operation_id: str,
    items: List[Dict[str, Any]],
    *,
    config_manager: Any = None,
    unlink: Callable[[str], None] = os.unlink,
) -> Dict[str, List[Any]]:
    """Unlink the planned items of one operation, one journal write per step.

    The ``deleting`` state is persisted BEFORE the unlink on purpose: a process
    that dies mid-run leaves the item in a state
    :func:`reconcile_incomplete_deletes` can settle, instead of a file that is
    gone with nothing saying so. Only ``Exception`` is caught — a
    ``KeyboardInterrupt`` must keep propagating and leave the row as evidence.
    """
    deleted: List[str] = []
    failed: List[Dict[str, str]] = []

    for item in items:
        try:
            stat = os.stat(item["path"])
            root = _containing_root(item["path"], _library_roots(config_manager))
            unchanged = (
                root == item["root"]
                and os.path.isfile(item["path"])
                and int(stat.st_size) == item["size"]
                and int(stat.st_mtime_ns) == item["mtime_ns"]
            )
        except OSError as exc:
            unchanged = False
            validation_error = str(exc) or exc.__class__.__name__
        else:
            validation_error = "file_changed_after_preview"

        conn = database._get_connection()
        try:
            if not unchanged:
                conn.execute(
                    """UPDATE lib2_file_delete_items SET status='failed', error=?
                         WHERE operation_id=? AND resolved_path=?""",
                    (validation_error, operation_id, item["path"]),
                )
                conn.commit()
                failed.append({"path": item["path"], "error": validation_error})
                continue
            conn.execute(
                "UPDATE lib2_file_delete_operations SET status='executing' WHERE id=?",
                (operation_id,),
            )
            conn.execute(
                """UPDATE lib2_file_delete_items SET status='deleting', error=NULL
                     WHERE operation_id=? AND resolved_path=?""",
                (operation_id, item["path"]),
            )
            conn.commit()
        finally:
            conn.close()

        try:
            unlink(item["path"])
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or exc.__class__.__name__
            conn = database._get_connection()
            try:
                conn.execute(
                    """UPDATE lib2_file_delete_items SET status='failed', error=?
                         WHERE operation_id=? AND resolved_path=?""",
                    (error, operation_id, item["path"]),
                )
                conn.commit()
            finally:
                conn.close()
            failed.append({"path": item["path"], "error": error})
            continue

        conn = database._get_connection()
        try:
            _mark_file_rows_deleted(conn, item["file_ids"])
            conn.execute(
                """UPDATE lib2_file_delete_items
                      SET status='deleted', deleted_at=CURRENT_TIMESTAMP, error=NULL
                    WHERE operation_id=? AND resolved_path=?""",
                (operation_id, item["path"]),
            )
            conn.commit()
        finally:
            conn.close()
        deleted.append(item["path"])

    return {"deleted": deleted, "failed": failed}


def _plan_target(
    conn, target: Any, roots: List[str], config_manager: Any = None,
    *, require_library_root: bool = True,
) -> Dict[str, Any]:
    """Turn one caller-supplied target into a journal item, safe or not.

    A target is a path, or a dict carrying the resolved ``path`` plus whatever
    the caller already knows (``stored_path``, ``file_ids``, ``track_ids``).
    Whatever it does not know is looked up here, so a worker that only ever had
    a path still produces a journal row that names the catalogue rows it
    retired.
    """
    if isinstance(target, dict):
        raw_path = str(target.get("path") or "")
        stored_path = target.get("stored_path")
        file_ids = [int(v) for v in (target.get("file_ids") or [])]
        track_ids = [int(v) for v in (target.get("track_ids") or [])]
    else:
        raw_path = str(target or "")
        stored_path, file_ids, track_ids = None, [], []

    from core.library2.paths import resolve_lib2_path

    resolved = raw_path
    if resolved and not os.path.exists(resolved):
        resolved = resolve_lib2_path(resolved, config_manager=config_manager) or resolved
    real_path = os.path.realpath(resolved) if resolved else ""

    lookup_paths = [p for p in {raw_path, stored_path, resolved, real_path} if p]
    if not file_ids and lookup_paths:
        marks = ",".join("?" for _ in lookup_paths)
        rows = conn.execute(
            f"SELECT id, track_id FROM lib2_track_files WHERE path IN ({marks})",
            lookup_paths,
        ).fetchall()
        file_ids = [int(row[0]) for row in rows]
        track_ids = track_ids or [int(row[1]) for row in rows]

    item: Dict[str, Any] = {
        "path": real_path or raw_path,
        "stored_paths": [p for p in {stored_path or raw_path} if p],
        "file_ids": file_ids,
        "track_ids": track_ids,
        "root": None,
        "size": 0,
        "mtime_ns": None,
        "error": None,
    }
    if not real_path or not os.path.isfile(real_path):
        item["error"] = "file_not_found"
        return item
    root = _containing_root(real_path, roots)
    if not root and require_library_root:
        # Fail closed, per file: with no configured root containing it there is
        # nothing that says this path belongs to the library at all.
        item["error"] = "outside_configured_library_roots"
        return item
    try:
        stat = os.stat(real_path)
    except OSError as exc:
        item["error"] = str(exc) or "stat_failed"
        return item
    item.update(root=root, size=int(stat.st_size), mtime_ns=int(stat.st_mtime_ns))
    return item


def delete_files_journaled(
    database,
    *,
    targets: List[Any],
    entity_type: str,
    entity_id: int,
    actor: str,
    actor_profile_id: Optional[int] = None,
    config_manager: Any = None,
    unlink: Callable[[str], None] = os.unlink,
    require_library_root: bool = True,
) -> Dict[str, Any]:
    """Delete files by path, through the ADR-05 journal.

    The entity-scoped :func:`delete_entity_files` is the user-facing command:
    it previews, hands the user a token and revalidates it. A maintenance job
    has no preview to revalidate — it has a finding, a path and a decision the
    user already approved (or, later, a policy that approved it). This is that
    command: same containment rule, same journal, same statuses, same crash
    recovery, without the token dance.

    ``entity_type``/``entity_id`` are what the History feed filters on
    (``albums``/``artists``), so a deletion made by a job shows up on the
    album's own timeline rather than nowhere. ``actor`` says who did it —
    ``repair:<job_id>`` for the worker, ``user`` for a person.

    ``require_library_root`` is what the ADR-05 dialog enforces and what the
    maintenance worker does NOT: it already applies
    :func:`fuzzy_resolved_path_is_deletable`, which is stricter for a path the
    resolver guessed and deliberately laxer for a path the catalogue names
    exactly — a library whose folders were never listed in
    ``library.music_paths`` still has deletable files. Journalling must not
    quietly change WHICH files a job may delete; that decision belongs to
    turning unattended deletion on, not to writing it down.
    """
    # Before recovery, not after: a worker can reach this on a database whose
    # journal tables were never created, and "no such table" must not be how a
    # delete fails.
    conn = database._get_connection()
    try:
        ensure_file_delete_schema(conn.cursor())
        conn.commit()
    finally:
        conn.close()

    reconcile_incomplete_deletes(database)
    roots = _library_roots(config_manager)

    conn = database._get_connection()
    try:
        planned = [
            _plan_target(conn, target, roots, config_manager,
                         require_library_root=require_library_root)
            for target in targets
        ]
    finally:
        conn.close()

    ok = [item for item in planned if not item["error"]]
    rejected = [item for item in planned if item["error"]]

    operation_id = uuid.uuid4().hex
    conn = database._get_connection()
    try:
        ensure_file_delete_schema(conn.cursor())
        conn.execute(
            """INSERT INTO lib2_file_delete_operations(
                   id, entity_type, entity_id, preview_token, status,
                   file_count, total_size, mode, actor, actor_profile_id)
               VALUES(?,?,?,'', 'planned', ?,?, 'permanent', ?,?)""",
            (
                operation_id,
                str(entity_type),
                int(entity_id),
                len(planned),
                sum(int(item["size"] or 0) for item in ok),
                str(actor or "user"),
                actor_profile_id,
            ),
        )
        for item in planned:
            conn.execute(
                """INSERT INTO lib2_file_delete_items(
                       operation_id, file_ids_json, stored_paths_json,
                       resolved_path, root_path, size, mtime_ns, status, error)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    operation_id,
                    json.dumps(item["file_ids"]),
                    json.dumps(item["stored_paths"]),
                    item["path"],
                    item["root"] or "",
                    item["size"],
                    item["mtime_ns"],
                    "failed" if item["error"] else "planned",
                    item["error"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    outcome = _execute_delete_items(
        database, operation_id, ok, config_manager=config_manager, unlink=unlink,
    )

    conn = database._get_connection()
    try:
        _finish_operation(conn, operation_id)
        conn.commit()
    finally:
        conn.close()

    return {
        "operation_id": operation_id,
        "deleted": outcome["deleted"],
        "failed": [
            *({"path": item["path"], "error": item["error"]} for item in rejected),
            *outcome["failed"],
        ],
        "track_ids": sorted({
            track_id for item in ok for track_id in item["track_ids"]
        }),
    }


def delete_entity_files(
    database,
    *,
    entity: str,
    entity_id: int,
    preview_token: str,
    file_ids: Optional[List[int]] = None,
    config_manager: Any = None,
    unlink: Callable[[str], None] = os.unlink,
    actor: str = "user",
    actor_profile_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute an ADR-05 delete after revalidating the exact preview.

    ``file_ids`` must match whatever selection produced ``preview_token`` —
    passing a different selection than the one previewed naturally fails the
    stale-preview check below, same as any other scope drift.
    """
    if not isinstance(preview_token, str) or not preview_token:
        raise FileDeleteError("preview_token is required")
    reconcile_incomplete_deletes(database)
    preview = preview_entity_files(
        database,
        entity=entity,
        entity_id=entity_id,
        file_ids=file_ids,
        config_manager=config_manager,
    )
    if preview_token != preview["preview_token"]:
        raise FileDeleteError("File-delete preview is stale; review the files again", 409)
    if not preview["files"]:
        raise FileDeleteError("No physical files to delete", 409)
    if preview["unsafe_count"]:
        raise FileDeleteError(
            "Physical delete blocked: one or more files are outside a safe library root",
            409,
        )

    # Rows whose file is already gone have nothing to unlink. They are still
    # part of what the user asked to remove, so they are journalled and their
    # catalogue rows retired — they just do not go through the unlink loop, and
    # they do not block the files that ARE there.
    gone = [item for item in preview["files"] if item["reason"] == ALREADY_GONE]
    to_unlink = [item for item in preview["files"] if item["deletable"]]

    operation_id = uuid.uuid4().hex
    conn = database._get_connection()
    try:
        ensure_file_delete_schema(conn.cursor())
        conn.execute(
            """INSERT INTO lib2_file_delete_operations(
                   id, entity_type, entity_id, preview_token, status,
                   file_count, total_size, mode, actor, actor_profile_id)
               VALUES(?,?,?,?, 'planned', ?,?, 'permanent', ?,?)""",
            (
                operation_id,
                entity,
                int(entity_id),
                preview_token,
                preview["file_count"],
                preview["total_size"],
                str(actor or "user"),
                actor_profile_id,
            ),
        )
        for item in preview["files"]:
            already_gone = item["reason"] == ALREADY_GONE
            conn.execute(
                f"""INSERT INTO lib2_file_delete_items(
                        operation_id, file_ids_json, stored_paths_json,
                        resolved_path, root_path, size, mtime_ns, status,
                        error, deleted_at)
                    VALUES(?,?,?,?,?,?,?,?,?,
                           {"CURRENT_TIMESTAMP" if already_gone else "NULL"})""",
                (
                    operation_id,
                    json.dumps(item["file_ids"]),
                    json.dumps(item["stored_paths"]),
                    item["path"] or (item["stored_paths"] or [""])[0],
                    item["root"] or "",
                    item["size"],
                    item["mtime_ns"],
                    "missing" if already_gone else "planned",
                    "file was already gone from disk" if already_gone else None,
                ),
            )
        for item in gone:
            _mark_file_rows_deleted(conn, item["file_ids"])
        conn.commit()
    finally:
        conn.close()

    _execute_delete_items(
        database, operation_id, to_unlink,
        config_manager=config_manager, unlink=unlink,
    )

    conn = database._get_connection()
    try:
        _finish_operation(conn, operation_id)
        conn.commit()
        result = _operation_snapshot(conn, operation_id)
        result["track_ids"] = sorted({
            int(track_id)
            for item in preview["files"]
            for track_id in item["track_ids"]
        })
        return result
    finally:
        conn.close()


__all__ = [
    "FileDeleteError",
    "delete_entity_files",
    "delete_files_journaled",
    "ensure_file_delete_schema",
    "get_delete_operation",
    "preview_entity_files",
    "reconcile_incomplete_deletes",
    "remove_entity_file_records",
]
