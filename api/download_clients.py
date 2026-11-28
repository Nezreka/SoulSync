"""REST API for multiple download client instances and per-library routing (#2336).

Routes:
- GET    /api/download-clients/instances
- POST   /api/download-clients/instances
- DELETE /api/download-clients/instances/<kind>/<client_id>
- POST   /api/download-clients/instances/<kind>/<client_id>/test
- GET    /api/download-clients/routing
- PUT    /api/download-clients/routing/<library_key>
- DELETE /api/download-clients/routing/<library_key>
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from core.downloads import routing as R
from core.profile_context import admin_only
from core.torrent_clients import (
    delete_client as delete_torrent_client,
    get_adapter_for_client as get_torrent_adapter,
    list_configured_clients as list_torrent_clients,
    upsert_client as upsert_torrent_client,
)
from core.usenet_clients import (
    delete_client as delete_usenet_client,
    get_adapter_for_client as get_usenet_adapter,
    list_configured_clients as list_usenet_clients,
    upsert_client as upsert_usenet_client,
)
from utils.logging_config import get_logger

logger = get_logger("api.download_clients")

bp = Blueprint("download_clients_api", __name__)


def _video_libraries() -> List[Dict[str, Any]]:
    try:
        from api.video import get_video_db
        vdb = get_video_db()
        if hasattr(vdb, "list_libraries"):
            libs = vdb.list_libraries() or []
            return [
                {
                    "key": f"video_library:{lib['id']}",
                    "id": lib["id"],
                    "name": lib.get("name") or f"Library {lib['id']}",
                    "media_type": lib.get("media_type") or "video",
                    "download_category": lib.get("download_category"),
                    "download_save_path": lib.get("download_save_path"),
                }
                for lib in libs
                if isinstance(lib, dict) and lib.get("id") is not None
            ]
    except Exception:  # noqa: BLE001
        logger.debug("could not load video libraries for routing list", exc_info=True)
    return []


@bp.route("/api/download-clients/instances", methods=["GET"])
@admin_only
def list_instances():
    return jsonify({
        "success": True,
        "torrent": list_torrent_clients(),
        "usenet": list_usenet_clients(),
    })


@bp.route("/api/download-clients/instances", methods=["POST"])
@admin_only
def upsert_instance():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in ("torrent", "usenet"):
        return jsonify({"success": False, "error": "kind must be 'torrent' or 'usenet'"}), 400
    try:
        saved = upsert_torrent_client(body) if kind == "torrent" else upsert_usenet_client(body)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({
        "success": True,
        "client": saved,
        "torrent": list_torrent_clients(),
        "usenet": list_usenet_clients(),
    })


@bp.route("/api/download-clients/instances/<kind>/<client_id>", methods=["DELETE"])
@admin_only
def remove_instance(kind: str, client_id: str):
    k = str(kind or "").strip().lower()
    if k not in ("torrent", "usenet"):
        return jsonify({"success": False, "error": "kind must be 'torrent' or 'usenet'"}), 400
    deleted = delete_torrent_client(client_id) if k == "torrent" else delete_usenet_client(client_id)
    if not deleted:
        return jsonify({"success": False, "error": "Client not found or cannot delete default"}), 404
    return jsonify({
        "success": True,
        "torrent": list_torrent_clients(),
        "usenet": list_usenet_clients(),
    })


@bp.route("/api/download-clients/instances/<kind>/<client_id>/test", methods=["POST"])
@admin_only
def test_instance(kind: str, client_id: str):
    k = str(kind or "").strip().lower()
    if k not in ("torrent", "usenet"):
        return jsonify({"success": False, "error": "kind must be 'torrent' or 'usenet'"}), 400
    adapter = get_torrent_adapter(client_id) if k == "torrent" else get_usenet_adapter(client_id)
    if adapter is None or not adapter.is_configured():
        return jsonify({"success": False, "connected": False, "error": "Client is not configured"}), 400
    try:
        ok = bool(asyncio.run(adapter.check_connection()))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "connected": False, "error": str(exc)}), 200
    return jsonify({"success": True, "connected": ok})


@bp.route("/api/download-clients/routing", methods=["GET"])
@admin_only
def get_routing():
    return jsonify({
        "success": True,
        "libraries": R.list_all_routings(_video_libraries()),
        "torrent": list_torrent_clients(),
        "usenet": list_usenet_clients(),
    })


@bp.route("/api/download-clients/routing/<library_key>", methods=["PUT"])
@admin_only
def save_routing(library_key: str):
    body = request.get_json(silent=True) or {}
    try:
        overrides = R.set_library_overrides(library_key, body)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({
        "success": True,
        "library_key": library_key,
        "overrides": overrides,
        "effective": R.resolve_library_routing(library_key).to_dict(),
    })


@bp.route("/api/download-clients/routing/<library_key>", methods=["DELETE"])
@admin_only
def reset_routing(library_key: str):
    R.delete_library_overrides(library_key)
    return jsonify({
        "success": True,
        "library_key": library_key,
        "effective": R.resolve_library_routing(library_key).to_dict(),
    })
