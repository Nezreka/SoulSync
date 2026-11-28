"""Settings, Connections, Give back (#2335): the three switches, the
musicbrainz account link, what has been given so far, and the albums
musicbrainz doesn't have with their filled-in add release form.

all admin only: the switches and the musicbrainz account are the server's.
no answer ever carries a token."""

from __future__ import annotations

from contextlib import closing

from flask import Blueprint, jsonify, request

from core.giveback import acoustid_submit, common, musicbrainz_oauth, release_seed, worker
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.giveback")

bp = Blueprint("giveback", __name__)

config_manager = None
get_database = None

_SWITCHES = {
    'acoustid': common.ACOUSTID_SETTING,
    'isrc': common.ISRC_SETTING,
    'releases': common.RELEASES_SETTING,
}


def configure(*, config_manager_, get_database_):
    global config_manager, get_database
    config_manager = config_manager_
    get_database = get_database_


def _connect():
    return get_database()._get_connection()


def _overview():
    acoustid_ok, acoustid_why = acoustid_submit.ready(config_manager)
    out = {
        'success': True,
        'switches': {k: common.enabled(v, config_manager) for k, v in _SWITCHES.items()},
        'acoustid': {'ready': acoustid_ok, 'message': '' if acoustid_why == 'off' else acoustid_why},
        'musicbrainz': musicbrainz_oauth.status(config_manager),
    }
    out.update(worker.status(_connect))
    return out


@bp.route('/api/giveback', methods=['GET'])
@admin_only
def giveback_overview():
    try:
        return jsonify(_overview())
    except Exception as e:  # noqa: BLE001
        logger.error("give back overview failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not read what has been given back'}), 500


@bp.route('/api/giveback/settings', methods=['PUT'])
@admin_only
def giveback_settings():
    body = request.get_json(silent=True) or {}
    for key, setting in _SWITCHES.items():
        if key in body:
            if not isinstance(body[key], bool):
                return jsonify({'success': False, 'error': f'{key} must be on or off'}), 400
            config_manager.set(setting, body[key])
    if body.get('acoustid') is True or body.get('isrc') is True:
        worker.nudge()
    return jsonify(_overview())


@bp.route('/api/giveback/run', methods=['POST'])
@admin_only
def giveback_run():
    if not worker.anything_on(config_manager):
        return jsonify({'success': False, 'error': 'Turn on AcoustID or ISRCs first'}), 409
    worker.nudge()
    return jsonify({'success': True})


@bp.route('/api/giveback/musicbrainz/connect', methods=['POST'])
@admin_only
def giveback_mb_connect():
    res = musicbrainz_oauth.start(config_manager)
    if res.get('error'):
        return jsonify({'success': False, 'error': res['error']}), 409
    return jsonify({'success': True, 'url': res['url']})


@bp.route('/api/giveback/musicbrainz/code', methods=['POST'])
@admin_only
def giveback_mb_code():
    body = request.get_json(silent=True) or {}
    code = body.get('code') if isinstance(body.get('code'), str) else ''
    res = musicbrainz_oauth.finish(code, config_manager)
    if res.get('error'):
        return jsonify({'success': False, 'error': res['error']}), 400
    return jsonify(_overview())


@bp.route('/api/giveback/musicbrainz/disconnect', methods=['POST'])
@admin_only
def giveback_mb_disconnect():
    musicbrainz_oauth.disconnect(config_manager)
    return jsonify(_overview())


@bp.route('/api/giveback/releases', methods=['GET'])
@admin_only
def giveback_releases():
    if not common.enabled(common.RELEASES_SETTING, config_manager):
        return jsonify({'success': True, 'releases': []})
    try:
        with closing(_connect()) as conn:
            return jsonify({'success': True, 'releases': release_seed.missing_releases(conn)})
    except Exception as e:  # noqa: BLE001
        logger.error("missing releases failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not list the albums'}), 500


@bp.route('/api/giveback/releases/<int:album_id>/seed', methods=['GET'])
@admin_only
def giveback_release_seed(album_id: int):
    try:
        with closing(_connect()) as conn:
            seed = release_seed.seed_fields(conn, album_id)
    except Exception as e:  # noqa: BLE001
        logger.error("release seed failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not fill in the form'}), 500
    if not seed:
        return jsonify({'success': False, 'error': 'SoulSync has no source for that album'}), 404
    return jsonify({'success': True, **seed})


def create_blueprint():
    return bp
