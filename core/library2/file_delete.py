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
from pathlib import Path
from typing import Any, Dict, List, Optional


class FileDeleteError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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


def _scope_snapshot(database, entity: str, entity_id: int) -> tuple[str, List[Dict[str, Any]]]:
    """Read the exact owned-file scope and close SQLite before path I/O."""
    if entity not in ("artists", "albums"):
        raise FileDeleteError("Unsupported entity")
    conn = database._get_connection()
    try:
        if entity == "artists":
            entity_row = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (int(entity_id),)
            ).fetchone()
            if not entity_row:
                raise FileDeleteError("Artist not found", 404)
            rows = conn.execute(
                """SELECT tf.id AS file_id, tf.track_id, tf.path AS stored_path,
                          tf.size AS db_size, tf.file_state, t.title AS track_title,
                          al.id AS album_id, al.title AS album_title
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                     JOIN lib2_albums al ON al.id=t.album_id
                    WHERE al.primary_artist_id=? AND tf.file_state<>'deleted'
                    ORDER BY al.id, t.id, tf.id""",
                (int(entity_id),),
            ).fetchall()
            title = entity_row["name"]
        else:
            entity_row = conn.execute(
                "SELECT title FROM lib2_albums WHERE id=?", (int(entity_id),)
            ).fetchone()
            if not entity_row:
                raise FileDeleteError("Album not found", 404)
            rows = conn.execute(
                """SELECT tf.id AS file_id, tf.track_id, tf.path AS stored_path,
                          tf.size AS db_size, tf.file_state, t.title AS track_title,
                          al.id AS album_id, al.title AS album_title
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                     JOIN lib2_albums al ON al.id=t.album_id
                    WHERE al.id=? AND tf.file_state<>'deleted'
                    ORDER BY t.id, tf.id""",
                (int(entity_id),),
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
    config_manager: Any = None,
) -> Dict[str, Any]:
    """Build a deterministic, non-mutating physical-delete preview."""
    from core.library2.paths import resolve_lib2_path

    title, rows = _scope_snapshot(database, entity, entity_id)
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


__all__ = ["FileDeleteError", "preview_entity_files"]
