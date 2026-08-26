"""Automation endpoints - lifted from web_server.py.

the /api/automations family (list/create/update/group/bulk/delete/
duplicate/toggle/run/progress/history/blocks/test-notify), the master
pause switch, and /api/scripts for the run-script block's dropdown.
route bodies were already thin handlers over core/automation/api.py -
now the thin handlers live out here too.

bodies byte-identical; only the decorator changed. automation_engine is
injected as the object itself: it is bound once at boot (engine or
None), never rebound, and the wiring runs after that binding.
"""

import os
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from core.automation import api as _auto_api
from core.automation import blocks as _auto_blocks
from core.automation import signals as _auto_signals
from core.automation import progress as _auto_progress
from core.profile_context import admin_only, get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("api.automations")

# injected by configure()
get_database = None
config_manager = None
docker_resolve_path = None
automation_engine = None


def configure(*, get_database_, config_manager_, docker_resolve_path_,
              automation_engine_):
    global get_database, config_manager, docker_resolve_path, automation_engine
    get_database = get_database_
    config_manager = config_manager_
    docker_resolve_path = docker_resolve_path_
    automation_engine = automation_engine_


bp = Blueprint('automations', __name__)


@bp.route('/api/automations', methods=['GET'])
def list_automations():
    """List all automations for the current profile."""
    try:
        profile_id = session.get('profile_id', 1)
        return jsonify(_auto_api.list_automations(get_database(), profile_id))
    except Exception as e:
        logger.error(f"Error listing automations: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/master', methods=['GET'])
def get_automations_master():
    """The per-side global pause state ({music: bool, video: bool}).
    It gates whether ANY automation runs on that side — individual enabled
    flags are untouched, so un-pausing restores exactly what the user had."""
    try:
        from core.automation_engine import AutomationEngine
        return jsonify({side: (automation_engine.master_enabled(side) if automation_engine
                               else AutomationEngine.MASTER_DEFAULTS[side])
                        for side in ('music', 'video')})
    except Exception as e:
        logger.error(f"Error reading automations master state: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/master', methods=['POST'])
@admin_only
def set_automations_master():
    """Flip one side's global automation pause. Body: {side, enabled}.
    Admin-only — it silences every automation on a side, not just yours."""
    try:
        data = request.get_json(silent=True) or {}
        side = (data.get('side') or '').strip().lower()
        if side not in ('music', 'video'):
            return jsonify({"success": False, "error": "side must be music or video"}), 400
        if automation_engine is None:
            return jsonify({"success": False, "error": "Automation engine unavailable"}), 503
        enabled = bool(data.get('enabled'))
        automation_engine.set_master_enabled(side, enabled)   # persists to the engine DB
        logger.info("Automations master for %s side set to %s", side, 'ON' if enabled else 'PAUSED')
        return jsonify({"success": True, "side": side, "enabled": enabled})
    except Exception as e:
        logger.error(f"Error setting automations master state: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/automations', methods=['POST'])
def create_automation():
    """Create a new automation."""
    try:
        profile_id = session.get('profile_id', 1)
        body, status = _auto_api.create_automation(get_database(), automation_engine, profile_id, request.get_json())
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error creating automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>', methods=['GET'])
def get_automation(automation_id):
    """Get a single automation."""
    try:
        auto = _auto_api.get_automation(get_database(), automation_id)
        if auto is None:
            return jsonify({"error": "Automation not found"}), 404
        return jsonify(auto)
    except Exception as e:
        logger.error(f"Error getting automation: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/automations/<int:automation_id>', methods=['PUT'])
def update_automation_endpoint(automation_id):
    """Update an automation."""
    try:
        body, status = _auto_api.update_automation(get_database(), automation_engine, automation_id, request.get_json())
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error updating automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/group', methods=['PUT'])
def batch_update_automation_group():
    """Batch update group_name for multiple automations."""
    try:
        data = request.get_json()
        body, status = _auto_api.batch_update_group(get_database(), data.get('automation_ids', []), data.get('group_name'))
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error batch updating automation group: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/bulk-toggle', methods=['POST'])
def bulk_toggle_automations():
    """Bulk enable/disable multiple automations."""
    try:
        data = request.get_json()
        body, status = _auto_api.bulk_toggle(get_database(), automation_engine,
                                              data.get('automation_ids', []), data.get('enabled', True))
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error bulk toggling automations: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>', methods=['DELETE'])
def delete_automation_endpoint(automation_id):
    """Delete an automation. System automations cannot be deleted."""
    try:
        body, status = _auto_api.delete_automation(get_database(), automation_engine, automation_id)
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error deleting automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>/duplicate', methods=['POST'])
def duplicate_automation_endpoint(automation_id):
    """Duplicate an automation. System automations cannot be duplicated."""
    try:
        profile_id = session.get('profile_id', 1)
        body, status = _auto_api.duplicate_automation(get_database(), automation_engine, profile_id, automation_id)
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error duplicating automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>/toggle', methods=['POST'])
def toggle_automation_endpoint(automation_id):
    """Toggle an automation's enabled state."""
    try:
        body, status = _auto_api.toggle_automation(get_database(), automation_engine, automation_id)
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error toggling automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>/run', methods=['POST'])
def run_automation_endpoint(automation_id):
    """Manually trigger an automation."""
    try:
        body, status = _auto_api.run_automation(automation_engine, automation_id, get_current_profile_id())
        return jsonify(body), status
    except Exception as e:
        logger.error(f"Error running automation: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/progress', methods=['GET'])
def get_automation_progress():
    """Get current progress state for all running/recently finished automations."""
    try:
        return jsonify(_auto_progress.get_running_progress())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/automations/<int:automation_id>/history', methods=['GET'])
def get_automation_history(automation_id):
    """Get run history for a specific automation."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        return jsonify(_auto_api.get_history(get_database(), automation_id, limit=limit, offset=offset))
    except Exception as e:
        logger.error(f"Error getting automation history: {e}")
        return jsonify({"error": str(e)}), 500


def _collect_known_signals():
    """Collect all signal names used across automations (for autocomplete)."""
    return _auto_signals.collect_known_signals(get_database())

@bp.route('/api/scripts', methods=['GET'])
def list_available_scripts():
    """List executable scripts in the scripts directory."""
    try:
        scripts_dir = docker_resolve_path(config_manager.get('scripts.path', './scripts'))
        if not scripts_dir or not os.path.isdir(scripts_dir):
            return jsonify({'scripts': []})

        allowed_ext = {'.sh', '.py', '.bat', '.ps1', '.rb', '.pl', '.js'}
        scripts = []
        for fname in sorted(os.listdir(scripts_dir)):
            ext = os.path.splitext(fname)[1].lower()
            fpath = os.path.join(scripts_dir, fname)
            if os.path.isfile(fpath) and (ext in allowed_ext or os.access(fpath, os.X_OK)):
                scripts.append({
                    'name': fname,
                    'extension': ext,
                    'size': os.path.getsize(fpath),
                })
        return jsonify({'scripts': scripts})
    except Exception as e:
        return jsonify({'scripts': [], 'error': str(e)})


@bp.route('/api/automations/blocks', methods=['GET'])
def get_automation_blocks():
    """Return available block types for the automation builder sidebar.

    Music builder only — video-only blocks (scope='video') are filtered out
    so the music builder never offers a video action. The video side fetches
    its own scope via /api/video/automations/blocks."""
    scoped = _auto_blocks.blocks_for_scope('music')
    scoped['known_signals'] = _collect_known_signals()
    return jsonify(scoped)

@bp.route('/api/automations/test-notify', methods=['POST'])
def test_automation_notify():
    """Fire ONE notification step with sample variables — the builder's Test
    button. Sends for real (that's the point: prove the webhook/token works)
    but against sample data, without running any automation."""
    try:
        if automation_engine is None:
            return jsonify({'success': False, 'error': 'Automation engine not running'}), 400
        data = request.get_json(silent=True) or {}
        ntype = str(data.get('type') or '')
        config = data.get('config') or {}
        variables = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': 'Test Automation',
            'run_count': '1',
            'status': 'completed',
            'artist': 'Test Artist', 'title': 'Test Track', 'album': 'Test Album',
            'kind': 'movie', 'quality': '1080p', 'error': 'sample error text',
        }
        senders = {
            'discord_webhook': automation_engine._send_discord_notification,
            'pushbullet': automation_engine._send_pushbullet_notification,
            'telegram': automation_engine._send_telegram_notification,
            'webhook': automation_engine._send_webhook,
        }
        sender = senders.get(ntype)
        if sender is None:
            return jsonify({'success': False, 'error': f'Cannot test {ntype or "this step"}'}), 400
        sender(config, variables)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200


def create_blueprint() -> Blueprint:
    return bp
