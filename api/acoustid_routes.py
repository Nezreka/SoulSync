"""AcoustID account linking for the settings card (#2318).

``GET /api/acoustid/account`` says whether the person's own AcoustID key is
set and how far a link has got; with ``check=1`` it first asks AcoustID about
a pending link. ``POST /api/acoustid/link`` starts a link and
``POST /api/acoustid/unlink`` forgets the key. All admin only: the key is a
server setting. No answer ever carries a key."""

from __future__ import annotations

import socket

from flask import Blueprint, jsonify, request

from core import acoustid_account
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.acoustid")

bp = Blueprint("acoustid_account", __name__)

# injected by configure() at boot
config_manager = None


def configure(*, config_manager_):
    global config_manager
    config_manager = config_manager_


def _device() -> str:
    """what acoustid shows the person on the approve page"""
    try:
        host = socket.gethostname() or ''
    except OSError:
        host = ''
    return f"SoulSync on {host}"[:100] if host else "SoulSync"


@bp.route('/api/acoustid/account', methods=['GET'])
@admin_only
def acoustid_account_status():
    try:
        if request.args.get('check') in ('1', 'true') and acoustid_account.link_pending(config_manager):
            checked = acoustid_account.check_link(config_manager)
            out = acoustid_account.status(config_manager)
            if checked.get('error'):
                out['error'] = checked['error']
            return jsonify({'success': True, **out})
        return jsonify({'success': True, **acoustid_account.status(config_manager)})
    except Exception as e:  # noqa: BLE001
        logger.error("AcoustID account status failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not read the AcoustID account'}), 500


@bp.route('/api/acoustid/link', methods=['POST'])
@admin_only
def acoustid_request_link():
    body = request.get_json(silent=True) or {}
    return_url = body.get('return_url') if isinstance(body.get('return_url'), str) else ''
    try:
        res = acoustid_account.request_link(config_manager, device=_device(), return_url=return_url)
    except Exception as e:  # noqa: BLE001
        logger.error("AcoustID link request failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not start linking with AcoustID'}), 500
    if res.get('unavailable'):
        # no soulsync application key yet: a link made now would break later
        return jsonify({'success': False, 'error': res['error']}), 409
    if not res.get('ok'):
        return jsonify({'success': False, 'error': res.get('error') or 'AcoustID refused the link'}), 400
    return jsonify({'success': True, **acoustid_account.status(config_manager)})


@bp.route('/api/acoustid/unlink', methods=['POST'])
@admin_only
def acoustid_unlink():
    try:
        acoustid_account.unlink(config_manager)
    except Exception as e:  # noqa: BLE001
        logger.error("AcoustID unlink failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not forget the AcoustID key'}), 500
    return jsonify({'success': True, **acoustid_account.status(config_manager)})


def create_blueprint():
    return bp
