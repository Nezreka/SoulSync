"""Tools > Update File Locations: preview, apply and undo (core.library2.path_repoint).

One job at a time, in a background thread, polled through /status. The
preview's answer stays here so the page can show it again after a reload, and
an apply re-checks every record itself: the preview only tells the user what
to expect, it is never the list that gets written.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from flask import Blueprint, jsonify, request

from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("file_locations")

get_database: Optional[Callable[[], Any]] = None

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "status": "idle",        # idle | running | done | error
    "action": None,          # preview | apply | undo
    "checked": 0,
    "total": 0,
    "result": None,
    "preview": None,         # the last finished preview
    "error": None,
    "started_at": None,
    "finished_at": None,
    "stop": False,
}


def configure(*, get_database_: Callable[[], Any]) -> None:
    global get_database
    get_database = get_database_


def _public_state() -> Dict[str, Any]:
    with _lock:
        state = {k: v for k, v in _state.items() if k != "stop"}
    return state


def _start(action: str, work: Callable[[Callable[[int, int], None], Callable[[], bool]], Any]):
    with _lock:
        if _state["status"] == "running":
            return jsonify({"success": False, "error": "A file location job is already running"}), 409
        _state.update({"status": "running", "action": action, "checked": 0, "total": 0,
                       "result": None, "error": None, "started_at": time.time(),
                       "finished_at": None, "stop": False})

    def progress(done: int, total: int) -> None:
        with _lock:
            _state["checked"], _state["total"] = int(done), int(total)

    def should_stop() -> bool:
        with _lock:
            return bool(_state["stop"])

    def run() -> None:
        try:
            result = work(progress, should_stop)
            with _lock:
                _state["result"] = result
                if action == "preview":
                    _state["preview"] = result
                elif action in ("apply", "undo"):
                    # what the preview said is out of date now
                    _state["preview"] = None
                _state["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - surfaced on the card
            logger.error("file location %s failed: %s", action, exc, exc_info=True)
            with _lock:
                _state["status"] = "error"
                _state["error"] = str(exc)
        finally:
            with _lock:
                _state["finished_at"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"FileLocations-{action}").start()
    return jsonify({"success": True, "action": action})


def create_blueprint() -> Blueprint:
    bp = Blueprint("file_locations", __name__)

    @bp.route("/api/library/file-locations/status", methods=["GET"])
    @admin_only
    def file_locations_status():
        from core.library2.path_repoint import list_runs
        state = _public_state()
        try:
            state["runs"] = list_runs(get_database())
        except Exception as exc:  # noqa: BLE001
            logger.debug("file location runs unreadable: %s", exc)
            state["runs"] = []
        return jsonify(state)

    @bp.route("/api/library/file-locations/preview", methods=["POST"])
    @admin_only
    def file_locations_preview():
        from core.library2.path_repoint import preview
        database = get_database()
        return _start("preview", lambda progress, stop: preview(
            database, progress=progress, should_stop=stop))

    @bp.route("/api/library/file-locations/apply", methods=["POST"])
    @admin_only
    def file_locations_apply():
        from core.library2.path_repoint import walk
        body = request.get_json(silent=True) or {}
        groups = body.get("groups")
        if groups is not None:
            if not isinstance(groups, list) or not all(
                    isinstance(g, dict) and isinstance(g.get("old_root"), str)
                    and isinstance(g.get("new_root"), str) for g in groups):
                return jsonify({"success": False,
                                "error": "groups must be a list of {old_root, new_root}"}), 400
            groups = [(g["old_root"], g["new_root"]) for g in groups]
            if not groups:
                return jsonify({"success": False, "error": "No folders chosen"}), 400
        database = get_database()
        return _start("apply", lambda progress, stop: walk(
            database, apply=True, groups=groups, progress=progress, should_stop=stop))

    @bp.route("/api/library/file-locations/runs/<int:run_id>/undo", methods=["POST"])
    @admin_only
    def file_locations_undo(run_id: int):
        from core.library2.path_repoint import list_runs, undo_run
        database = get_database()
        runs = {int(r["id"]): r for r in list_runs(database, limit=200)}
        if run_id not in runs:
            return jsonify({"success": False, "error": "No such run"}), 404
        if runs[run_id]["status"] == "undone":
            return jsonify({"success": False, "error": "That run is already undone"}), 409
        return _start("undo", lambda progress, _stop: {
            "run_id": run_id, **undo_run(database, run_id, progress=progress)})

    @bp.route("/api/library/file-locations/stop", methods=["POST"])
    @admin_only
    def file_locations_stop():
        with _lock:
            if _state["status"] != "running":
                return jsonify({"success": False, "error": "Nothing is running"}), 409
            _state["stop"] = True
        return jsonify({"success": True})

    return bp


__all__ = ["configure", "create_blueprint"]
