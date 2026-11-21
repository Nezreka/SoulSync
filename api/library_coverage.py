"""library coverage: how much of the media server's music SoulSync can read.

GET    /api/library/coverage                 the last check (?refresh=1 runs a new one)
POST   /api/library/coverage/folder          {server_folder, local_folder?} one folder,
                                             optionally as if SoulSync saw it at local_folder
POST   /api/library/coverage/check-path      {path} is this folder there, readable, what's in it
GET    /api/library/path-mappings            folder overrides (?media_type=music|video)
POST   /api/library/path-mappings            {server_prefix, local_prefix, server_kind?, media_type?}
PUT    /api/library/path-mappings/<id>       same body
DELETE /api/library/path-mappings/<id>
POST   /api/library/path-mappings/<id>/verify  sample the files under it, store the result

every route is admin only: the answers are the server's folder layout.
"Add as a library folder" is the settings save of library.music_paths, the
same one the Additional Music Libraries list uses.
"""

from __future__ import annotations

import os
import threading
from contextlib import closing
from typing import Any, Callable, List, Optional

from flask import Blueprint, jsonify, request

from core.library import coverage, path_mappings
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.library_coverage")

bp = Blueprint("library_coverage", __name__)

_state: dict = {"get_database": None, "config_manager": None, "engine": None,
                "env_factory": None}
_run_lock = threading.Lock()

# mount points that are the container's own plumbing, never a music drive
_SYSTEM_MOUNTS = ("/proc", "/sys", "/dev", "/etc", "/run", "/tmp", "/var/lib", "/usr", "/boot")


def configure(*, get_database: Callable[[], Any], config_manager: Any = None,
              media_server_engine_getter: Optional[Callable[[], Any]] = None,
              env_factory: Optional[Callable[[], coverage.Env]] = None) -> None:
    """from web_server; tests pass fakes. env_factory replaces the real
    filesystem and settings view"""
    _state.update(get_database=get_database, config_manager=config_manager,
                  engine=media_server_engine_getter, env_factory=env_factory)
    path_mappings.configure(lambda: get_database()._get_connection())


def create_blueprint():
    return bp


# ---------------------------------------------------------------- helpers
def _conn():
    return closing(_state["get_database"]()._get_connection())


def _cfg(key, default=None):
    cm = _state.get("config_manager")
    if cm is None:
        return default
    try:
        value = cm.get(key, default)
    except Exception:  # noqa: BLE001
        return default
    return default if value is None else value


def _server():
    """(server kind, client) for the active media server"""
    getter = _state.get("engine")
    engine = getter() if getter else None
    if engine is None:
        return str(_cfg("active_media_server", "") or ""), None
    try:
        return str(engine.active_server or ""), engine.active_client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("active media server unavailable: %s", exc)
        return "", None


def library_dirs(conn=None) -> List[str]:
    """the folders SoulSync scans for music: the music library folder, every
    extra library folder and every profile's own library folder"""
    from core.imports.paths import docker_resolve_path
    raw = [_cfg("soulseek.transfer_path", "./Transfer")]
    extra = _cfg("library.music_paths", []) or []
    raw.extend([extra] if isinstance(extra, str) else list(extra))
    if conn is not None:
        try:
            from core.library2.library_roots import current_roots
            raw.extend(current_roots(conn.cursor()).keys())
        except Exception as exc:  # noqa: BLE001 - no roots table yet
            logger.debug("library roots unreadable: %s", exc)
    out: List[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        resolved = docker_resolve_path(value.strip()).rstrip("/\\") or "/"
        if resolved not in out:
            out.append(resolved)
    return out


def _env(conn, overrides=None) -> coverage.Env:
    factory = _state.get("env_factory")
    if factory is not None:
        env = factory()
        if overrides is not None:
            env.overrides = overrides
        return env
    from core.library.server_paths import local_folder_for
    from core.onboarding.environment import is_docker
    from core.onboarding.paths import mount_points
    docker = bool(is_docker())
    mounts = []
    if docker:
        mounts = [m for m in mount_points() if m.startswith("/") and m != "/"
                  and not m.startswith(_SYSTEM_MOUNTS)]
    return coverage.Env(
        library_dirs=library_dirs(conn), mounts=mounts, docker=docker,
        learned=local_folder_for,
        overrides=(overrides if overrides is not None
                   else path_mappings.list_mappings(conn, "music", enabled_only=True)))


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- coverage
def run_now() -> dict:
    """one full check, remembered for the Help health line"""
    kind, client = _server()
    if not kind:
        return {"server": "", "folders": [], "total_tracks": 0, "reachable_pct": None,
                "message": "No media server is linked, so there is nothing to compare with."}
    with _run_lock, _conn() as conn:
        result = coverage.run_check(conn, kind, client, _env(conn))
    coverage.remember(result)
    return result


@bp.route("/api/library/coverage", methods=["GET"])
@admin_only
def get_coverage():
    if request.args.get("refresh") not in ("1", "true"):
        last = coverage.last_result()
        if last:
            return jsonify({"success": True, **last})
    try:
        return jsonify({"success": True, **run_now()})
    except Exception as exc:  # noqa: BLE001
        logger.error("library coverage check failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "The check failed. The app log has the details."}), 500


@bp.route("/api/library/coverage/folder", methods=["POST"])
@admin_only
def check_one_folder():
    body = _body()
    server_folder = str(body.get("server_folder") or "").strip()
    if not server_folder:
        return jsonify({"success": False, "error": "server_folder is required"}), 400
    local = str(body.get("local_folder") or "").strip()
    kind, _client = _server()
    if not kind:
        return jsonify({"success": False, "error": "No media server is linked"}), 400
    with _conn() as conn:
        overrides = None
        if local:
            # as if this were saved: the typed folder is the only override
            overrides = [{"server_prefix": server_folder, "local_prefix": local,
                          "server_kind": kind, "enabled": True}]
        result = coverage.check_folder(conn, kind, server_folder, _env(conn, overrides),
                                       library=str(body.get("library") or ""))
    return jsonify({"success": True, "folder": result})


@bp.route("/api/library/coverage/check-path", methods=["POST"])
@admin_only
def check_path():
    path = str(_body().get("path") or "").strip()
    if not path:
        return jsonify({"success": False, "error": "path is required"}), 400
    out = {"success": True, "path": path, "exists": os.path.exists(path),
           "is_dir": os.path.isdir(path)}
    out["readable"] = bool(out["is_dir"] and os.access(path, os.R_OK | os.X_OK))
    children: List[str] = []
    if out["readable"]:
        try:
            with os.scandir(path) as it:
                for i, entry in enumerate(it):
                    if i >= 1000:
                        break
                    children.append(entry.name)
        except OSError:
            out["readable"] = False
    out["entries"] = len(children)
    out["sample"] = sorted(children)[:8]
    with _conn() as conn:
        dirs = _env(conn).library_dirs
    out["library_folder"] = coverage.library_folder_for(path, dirs) if out["is_dir"] else None
    return jsonify(out)


# ---------------------------------------------------------------- overrides
@bp.route("/api/library/path-mappings", methods=["GET"])
@admin_only
def list_overrides():
    media_type = request.args.get("media_type") or None
    with _conn() as conn:
        return jsonify({"success": True, "mappings": path_mappings.list_mappings(conn, media_type)})


def _save(mapping_id=None):
    body = _body()
    if not body.get("server_kind"):
        body["server_kind"] = _server()[0] if body.get("this_server") else ""
    try:
        with _conn() as conn:
            new_id = path_mappings.save_mapping(conn, body, mapping_id)
            row = next((m for m in path_mappings.list_mappings(conn) if m["id"] == new_id), None)
    except path_mappings.MappingError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    coverage.forget()
    return jsonify({"success": True, "mapping": row})


@bp.route("/api/library/path-mappings", methods=["POST"])
@admin_only
def create_override():
    return _save()


@bp.route("/api/library/path-mappings/<int:mapping_id>", methods=["PUT"])
@admin_only
def update_override(mapping_id: int):
    return _save(mapping_id)


@bp.route("/api/library/path-mappings/<int:mapping_id>", methods=["DELETE"])
@admin_only
def delete_override(mapping_id: int):
    with _conn() as conn:
        gone = path_mappings.delete_mapping(conn, mapping_id)
    coverage.forget()
    if not gone:
        return jsonify({"success": False, "error": "That override is gone"}), 404
    return jsonify({"success": True})


@bp.route("/api/library/path-mappings/<int:mapping_id>/verify", methods=["POST"])
@admin_only
def verify_override(mapping_id: int):
    kind, _client = _server()
    with _conn() as conn:
        row = next((m for m in path_mappings.list_mappings(conn) if m["id"] == mapping_id), None)
        if row is None:
            return jsonify({"success": False, "error": "That override is gone"}), 404
        server = row["server_kind"] or kind
        if not server:
            return jsonify({"success": False, "error": "No media server is linked"}), 400
        result = coverage.check_folder(conn, server, row["server_prefix"], _env(conn, [row]))
        stats = {k: result.get(k) for k in ("sampled", "readable", "readable_pct", "reasons",
                                             "tracks", "status")}
        path_mappings.record_verify(conn, mapping_id, stats)
    return jsonify({"success": True, "folder": result})


__all__ = ["bp", "configure", "create_blueprint", "library_dirs", "run_now"]
