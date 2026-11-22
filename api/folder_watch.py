"""Live folder watching: what is watched and how (Settings > Library > Folders).

GET  /api/library/folder-watch          every watched folder, its mode (live,
                                        polling every N minutes, off), why, and
                                        the last change seen
POST /api/library/folder-watch/refresh  read the settings again now (after a
                                        save), then the same answer

admin only: the answer is the server's folder layout.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.library import folder_watch
from core.profile_context import admin_only

bp = Blueprint("folder_watch", __name__)


def create_blueprint():
    return bp


@bp.route("/api/library/folder-watch", methods=["GET"])
@admin_only
def get_folder_watch():
    return jsonify({"success": True, **folder_watch.status()})


@bp.route("/api/library/folder-watch/refresh", methods=["POST"])
@admin_only
def refresh_folder_watch():
    service = folder_watch.get_service()
    if service is not None:
        # on the request thread: a few stats per folder, and the answer then
        # shows the setting that was just saved
        service.refresh()
    return jsonify({"success": True, **folder_watch.status()})
