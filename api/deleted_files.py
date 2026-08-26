"""Deleted-files manager endpoints - the browsable music recycle bin.

files the repair worker and duplicate cleaner remove go to
<transfer>/.deleted instead of dying. these routes let the downloads page
list that folder, restore files to where they came from, purge them for
real, and set the retention window. all the actual filesystem logic lives
in core/library/deleted_quarantine.py - this is the thin http layer.
"""

from flask import Blueprint, jsonify, request

from core.library import deleted_quarantine
from utils.logging_config import get_logger

logger = get_logger("api.deleted_files")

RETENTION_KEY = "library.deleted_keep_days"

# injected by configure()
config_manager = None
docker_resolve_path = None


def configure(*, config_manager_, docker_resolve_path_):
    global config_manager, docker_resolve_path
    config_manager = config_manager_
    docker_resolve_path = docker_resolve_path_


def _transfer_folder() -> str:
    return docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))


def _keep_days() -> float:
    try:
        return float(config_manager.get(RETENTION_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def create_blueprint() -> Blueprint:
    bp = Blueprint('deleted_files', __name__)

    @bp.route('/api/deleted-files', methods=['GET'])
    def list_deleted_files():
        try:
            keep_days = _keep_days()
            result = deleted_quarantine.list_entries(_transfer_folder(), keep_days=keep_days)
            result.update({"success": True, "keep_days": keep_days})
            return jsonify(result)
        except Exception as e:
            logger.error(f"[DeletedFiles] list failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route('/api/deleted-files/restore', methods=['POST'])
    def restore_deleted_files():
        try:
            payload = request.get_json(silent=True) or {}
            ids = payload.get('ids')
            if not isinstance(ids, list) or not ids:
                return jsonify({"success": False, "error": "ids required"}), 400
            result = deleted_quarantine.restore_entries(_transfer_folder(), ids)
            result["success"] = True
            return jsonify(result)
        except Exception as e:
            logger.error(f"[DeletedFiles] restore failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route('/api/deleted-files/purge', methods=['POST'])
    def purge_deleted_files():
        try:
            payload = request.get_json(silent=True) or {}
            ids = payload.get('ids')
            purge_all = bool(payload.get('all'))
            if not purge_all and (not isinstance(ids, list) or not ids):
                return jsonify({"success": False, "error": "ids or all required"}), 400
            result = deleted_quarantine.purge_entries(
                _transfer_folder(), ids if not purge_all else None, purge_all=purge_all)
            result["success"] = True
            return jsonify(result)
        except Exception as e:
            logger.error(f"[DeletedFiles] purge failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route('/api/deleted-files/retention', methods=['POST'])
    def set_deleted_retention():
        try:
            payload = request.get_json(silent=True) or {}
            days = payload.get('days')
            try:
                days = float(days)
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "days must be a number"}), 400
            if days < 0 or days > 3650:
                return jsonify({"success": False, "error": "days must be between 0 and 3650"}), 400
            config_manager.set(RETENTION_KEY, days)
            return jsonify({"success": True, "keep_days": days})
        except Exception as e:
            logger.error(f"[DeletedFiles] retention save failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    return bp
