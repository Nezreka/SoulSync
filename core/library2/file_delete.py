"""ADR-05 physical-file delete preview and root-safety boundary.

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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_file_delete_items_operation "
        "ON lib2_file_delete_items(operation_id, status)"
    )


def _library_roots(config_manager: Any = None) -> List[str]:
    """Return existing, canonical roots explicitly configured by the user."""
    try:
        if config_manager is None:
            from config.settings import config_manager as _config_manager
            config_manager = _config_manager
        configured = config_manager.get("library.music_paths", []) or []
    except Exception:  # noqa: BLE001
        configured = []
    if isinstance(configured, str):
        configured = [configured]

    from core.imports.paths import docker_resolve_path

    roots: List[str] = []
    for raw in configured:
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
            entity_row = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (int(entity_id),)
            ).fetchone()
            if not entity_row:
                raise FileDeleteError("Artist not found", 404)
            rows = conn.execute(
                f"""SELECT tf.id AS file_id, tf.track_id, tf.path AS stored_path,
                          tf.size AS db_size, tf.file_state, t.title AS track_title,
                          al.id AS album_id, al.title AS album_title
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                     JOIN lib2_albums al ON al.id=t.album_id
                    WHERE al.primary_artist_id=? AND tf.file_state<>'deleted'{id_filter}
                    ORDER BY al.id, t.id, tf.id""",
                (int(entity_id), *id_params),
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
                "size": None,
                "mtime_ns": None,
                "deletable": False,
                "reason": "path_unresolved",
                "album_id": row["album_id"],
                "album_title": row["album_title"],
                "track_titles": [],
            },
        )
        item["file_ids"].append(int(row["file_id"]))
        item["track_ids"].append(int(row["track_id"]))
        item["stored_paths"].append(row["stored_path"])
        item["track_titles"].append(row["track_title"])
        if real_path:
            root = _containing_root(real_path, roots)
            item["root"] = root
            if not root:
                item["reason"] = "outside_configured_library_roots"
            elif not os.path.isfile(real_path):
                item["reason"] = "not_a_regular_file"
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
        "unsafe_count": sum(1 for item in files if not item["deletable"]),
        "total_size": sum(int(item["size"] or 0) for item in files if item["deletable"]),
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


def delete_entity_files(
    database,
    *,
    entity: str,
    entity_id: int,
    preview_token: str,
    file_ids: Optional[List[int]] = None,
    config_manager: Any = None,
    unlink: Callable[[str], None] = os.unlink,
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

    operation_id = uuid.uuid4().hex
    conn = database._get_connection()
    try:
        ensure_file_delete_schema(conn.cursor())
        conn.execute(
            """INSERT INTO lib2_file_delete_operations(
                   id, entity_type, entity_id, preview_token, status,
                   file_count, total_size)
               VALUES(?,?,?,?, 'planned', ?,?)""",
            (
                operation_id,
                entity,
                int(entity_id),
                preview_token,
                preview["file_count"],
                preview["total_size"],
            ),
        )
        for item in preview["files"]:
            conn.execute(
                """INSERT INTO lib2_file_delete_items(
                       operation_id, file_ids_json, stored_paths_json,
                       resolved_path, root_path, size, mtime_ns, status)
                   VALUES(?,?,?,?,?,?,?, 'planned')""",
                (
                    operation_id,
                    json.dumps(item["file_ids"]),
                    json.dumps(item["stored_paths"]),
                    item["path"],
                    item["root"],
                    item["size"],
                    item["mtime_ns"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    for item in preview["files"]:
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
            conn = database._get_connection()
            try:
                conn.execute(
                    """UPDATE lib2_file_delete_items SET status='failed', error=?
                         WHERE operation_id=? AND resolved_path=?""",
                    (str(exc) or exc.__class__.__name__, operation_id, item["path"]),
                )
                conn.commit()
            finally:
                conn.close()
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

    conn = database._get_connection()
    try:
        _finish_operation(conn, operation_id)
        conn.commit()
        return _operation_snapshot(conn, operation_id)
    finally:
        conn.close()


__all__ = [
    "FileDeleteError",
    "delete_entity_files",
    "ensure_file_delete_schema",
    "get_delete_operation",
    "preview_entity_files",
    "reconcile_incomplete_deletes",
]
