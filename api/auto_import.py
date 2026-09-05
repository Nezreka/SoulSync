"""Auto-import watcher endpoints, lifted out of web_server.py.

Status and control for the staging auto-import watcher. The worker is a boot
global that can be None when its init failed - read through a getter."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("api.auto_import")

bp = Blueprint("auto_import", __name__)

# Injected by configure() at boot.
get_database = None
config_manager = None
_auto_import_worker = lambda: None   # noqa: E731


def configure(*, get_database, config_manager, _auto_import_worker):
    globals()['get_database'] = get_database
    globals()['config_manager'] = config_manager
    globals()['_auto_import_worker'] = _auto_import_worker


def create_blueprint():
    return bp


@bp.route('/api/auto-import/status', methods=['GET'])
def auto_import_status():
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    return jsonify({"success": True, **_auto_import_worker().get_status()})


@bp.route('/api/auto-import/toggle', methods=['POST'])
def auto_import_toggle():
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    data = request.get_json() or {}
    enabled = data.get('enabled', not _auto_import_worker().running)
    if enabled:
        config_manager.set('auto_import.enabled', True)
        if not _auto_import_worker().running:
            # Never a bare .start(): a v2 upgrade still in progress must hold
            # the worker back, or it imports against a half-migrated catalogue.
            from core.library2.migration_gate import defer_or_start
            defer_or_start(_auto_import_worker(), get_database())
    else:
        config_manager.set('auto_import.enabled', False)
        _auto_import_worker().stop()
    return jsonify({"success": True, "enabled": enabled})


@bp.route('/api/auto-import/settings', methods=['GET', 'POST'])
def auto_import_settings():
    def normalize_quality_profile_id(raw_profile_id):
        if raw_profile_id in (None, '', 0, '0'):
            return None
        try:
            profile_id = int(raw_profile_id)
        except (TypeError, ValueError):
            return None
        return profile_id if profile_id > 0 else None

    if request.method == 'GET':
        return jsonify({
            "success": True,
            "enabled": config_manager.get('auto_import.enabled', False),
            "scan_interval": config_manager.get('auto_import.scan_interval', 60),
            "confidence_threshold": config_manager.get('auto_import.confidence_threshold', 0.9),
            "auto_process": config_manager.get('auto_import.auto_process', True),
            # Per-context quality profile override (see core/_auto_import_worker().py
            # _process_matches) — None/0 means "use the app-wide default profile",
            # same as every other context that doesn't specify its own.
            "quality_profile_id": normalize_quality_profile_id(config_manager.get('auto_import.quality_profile_id')),
        })
    data = request.get_json() or {}
    if 'quality_profile_id' in data:
        raw_profile_id = data.get('quality_profile_id')
        if raw_profile_id in (None, '', 0, '0'):
            data['quality_profile_id'] = None
        else:
            profile_id = normalize_quality_profile_id(raw_profile_id)
            if profile_id is None:
                return jsonify({"success": False, "error": "Invalid quality profile"}), 400

            try:
                from database.music_database import MusicDatabase
                db = MusicDatabase()
                profile_ids = {int(profile.get('id')) for profile in db.list_quality_profiles()}
            except Exception as e:
                logger.error(f"Error validating Auto-Import quality profile: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

            if profile_id not in profile_ids:
                return jsonify({"success": False, "error": "Quality profile not found"}), 404
            data['quality_profile_id'] = profile_id

    for key in ['enabled', 'scan_interval', 'confidence_threshold', 'auto_process', 'quality_profile_id']:
        if key in data:
            config_manager.set(f'auto_import.{key}', data[key])
    return jsonify({"success": True})


@bp.route('/api/auto-import/results', methods=['GET'])
def auto_import_results():
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    status_filter = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    results = _auto_import_worker().get_results(status_filter=status_filter, limit=limit)
    return jsonify({"success": True, "results": results})


@bp.route('/api/auto-import/approve/<int:item_id>', methods=['POST'])
def auto_import_approve(item_id):
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    result = _auto_import_worker().approve_item(item_id)
    if result.get('success') and _auto_import_worker().running:
        threading.Thread(
            target=_auto_import_worker().trigger_scan,
            daemon=True,
            name='AutoImportApprovalScan',
        ).start()
    return jsonify(result)


@bp.route('/api/auto-import/reject/<int:item_id>', methods=['POST'])
def auto_import_reject(item_id):
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    return jsonify(_auto_import_worker().reject_item(item_id))


@bp.route('/api/auto-import/scan-now', methods=['POST'])
def auto_import_scan_now():
    """Trigger an immediate scan cycle.

    Routes through `trigger_scan()`, the canonical entry point shared
    with the worker's timer loop. Pre-refactor this endpoint spawned
    a fresh `_scan_cycle` thread per click — emergent parallelism
    that grew unbounded with each click and produced racy access to
    candidate-tracking state. Post-refactor:

    - Manual triggers + the timer loop share one scan-lock, so only
      one scan runs at a time
    - Per-candidate processing happens on the worker's bounded
      `ThreadPoolExecutor` (default 3 workers — predictable
      concurrency, configurable via `auto_import.max_workers`)
    - Multiple "Scan Now" clicks while a scan is in flight no-op
      instead of stacking up parallel scanners

    Runs the scan in a background thread so the HTTP response returns
    immediately — `trigger_scan()` itself is fast (just enumeration +
    submit), but a slow filesystem walk on a large staging dir could
    still hold the request thread for seconds. Detached thread is
    safe: scan-lock prevents duplicate work, executor handles
    per-candidate processing.
    """
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    if not _auto_import_worker().running:
        return jsonify({"success": False, "error": "Auto-import is not running"}), 400
    threading.Thread(
        target=_auto_import_worker().trigger_scan,
        daemon=True,
        name='AutoImportScanNow',
    ).start()
    return jsonify({"success": True})


@bp.route('/api/auto-import/approve-all', methods=['POST'])
def auto_import_approve_all():
    """Approve all pending review items."""
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    try:
        results = _auto_import_worker().get_results(status_filter='pending_review', limit=200)
        count = 0
        for r in results:
            result = _auto_import_worker().approve_item(r['id'])
            if result.get('success'):
                count += 1
        if count and _auto_import_worker().running:
            threading.Thread(
                target=_auto_import_worker().trigger_scan,
                daemon=True,
                name='AutoImportApprovalScan',
            ).start()
        return jsonify({"success": True, "count": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/auto-import/clear-completed', methods=['POST'])
def auto_import_clear_completed():
    """Remove completed/imported items from history.

    `processing` rows are included so zombie entries (server restarted
    mid-import → `_record_in_progress` row never got finalized) get
    swept. Live in-flight imports are protected by intersecting against
    `_snapshot_active()` — anything currently registered in the worker's
    `_active_imports` map keeps its row. `pending_review` is left out so
    user still has to approve/reject those explicitly.
    """
    if not _auto_import_worker():
        return jsonify({"success": False, "error": "Auto-import not available"}), 500
    try:
        active_hashes = {e['folder_hash'] for e in _auto_import_worker()._snapshot_active()}
        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            base_sql = (
                "DELETE FROM auto_import_history "
                "WHERE status IN ('completed', 'partial', 'approved', 'failed', "
                "'needs_identification', 'rejected', 'processing')"
            )
            if active_hashes:
                placeholders = ','.join('?' * len(active_hashes))
                cursor.execute(
                    f"{base_sql} AND folder_hash NOT IN ({placeholders})",
                    tuple(active_hashes),
                )
            else:
                cursor.execute(base_sql)
            count = cursor.rowcount
            conn.commit()
        return jsonify({"success": True, "count": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


