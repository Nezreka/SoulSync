"""setup flow endpoints. read-only apart from the checklist ticks.

GET  /api/onboarding/environment   docker or not, and the folders that fit (?pretend=docker|bare)
POST /api/onboarding/discover      {services} -> {found: {service: url}}
POST /api/onboarding/check-paths   {paths: {key: path}} -> per-path state
POST /api/onboarding/probe         {service, config} -> {ok, message, detail}
POST /api/onboarding/libraries     {server, config} -> music/movies/tv libraries
POST /api/onboarding/top-artists   {server, config, library_id} -> {artists}
GET  /api/onboarding/checklist     the dashboard "finish setting up" card
POST /api/onboarding/checklist     {done: id} or {dismissed: bool}

probe, libraries and top-artists connect to whatever url they're given, so
they're admin only like the rest of the settings routes.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from core.onboarding import checklist, discovery, environment, paths
from core.onboarding.probes import Prober, probe
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.onboarding")

bp = Blueprint("onboarding", __name__)

config_manager = None
get_database = None
_resolve_url = None


def configure(*, config_manager_, get_database_, docker_resolve_url=None):
    global config_manager, get_database, _resolve_url
    config_manager = config_manager_
    get_database = get_database_
    _resolve_url = docker_resolve_url


def _prober() -> Prober:
    return Prober(resolve_url=_resolve_url)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.route('/api/onboarding/environment', methods=['GET'])
@admin_only
def onboarding_environment():
    env = environment.describe()
    # the developer preview can pretend to be the other kind of install
    pretend = request.args.get('pretend', '')
    if pretend in ('docker', 'bare'):
        docker = pretend == 'docker'
        env = {"docker": docker, "platform": "docker" if docker else "linux",
               "folders": environment.default_folders(docker)}
    return jsonify(env)


@bp.route('/api/onboarding/discover', methods=['POST'])
@admin_only
def onboarding_discover():
    services = _body().get('services') or list(discovery.SERVICES)
    if not isinstance(services, list):
        return jsonify({"error": "services must be a list"}), 400
    try:
        found = discovery.discover([str(s) for s in services], docker=environment.is_docker())
    except Exception as exc:  # noqa: BLE001 - discovery is a nicety, setup works without it
        logger.debug("onboarding discovery failed: %s", exc)
        found = {}
    return jsonify({"found": found})


@bp.route('/api/onboarding/check-paths', methods=['POST'])
@admin_only
def onboarding_check_paths():
    requested = _body().get('paths') or {}
    if not isinstance(requested, dict):
        return jsonify({"error": "paths must be an object"}), 400
    clean = {str(k): str(v or '') for k, v in requested.items()}
    return jsonify({"results": paths.check_paths(clean, docker=environment.is_docker())})


@bp.route('/api/onboarding/probe', methods=['POST'])
@admin_only
def onboarding_probe():
    body = _body()
    cfg = body.get('config') or {}
    if not isinstance(cfg, dict):
        return jsonify({"ok": False, "message": "config must be an object"}), 400
    return jsonify(probe(str(body.get('service') or ''), cfg, _prober()))


@bp.route('/api/onboarding/libraries', methods=['POST'])
@admin_only
def onboarding_libraries():
    body = _body()
    cfg = body.get('config') or {}
    return jsonify(_prober().libraries(str(body.get('server') or ''), cfg if isinstance(cfg, dict) else {}))


@bp.route('/api/onboarding/top-artists', methods=['POST'])
@admin_only
def onboarding_top_artists():
    body = _body()
    cfg = body.get('config') or {}
    names = _prober().top_artists(str(body.get('server') or ''), cfg if isinstance(cfg, dict) else {},
                                  str(body.get('library_id') or ''))
    return jsonify({"artists": names})


def _tmdb_key() -> str:
    try:
        from api.video import get_video_db
        return get_video_db().get_setting("tmdb_api_key") or ""
    except Exception:  # noqa: BLE001 - a missing video db just means no key
        return ""


def _profile_count() -> int:
    try:
        return len(get_database().get_all_profiles() or [])
    except Exception:  # noqa: BLE001
        return 1


# slskd is asked about its shares at most this often while the answer is no
_SHARES_RECHECK_SECONDS = 120
_shares_checked_at = 0.0


def _slskd_shares_now() -> bool:
    """true once slskd shares any files. a yes is remembered as a tick, so slskd
    stops being asked; a no is asked again a couple of minutes later"""
    global _shares_checked_at
    done = config_manager.get('setup.checklist_done', []) or []
    if 'slskd_shares' in done:
        return True
    url = config_manager.get('soulseek.slskd_url', '') or ''
    now = time.monotonic()
    if not url or now - _shares_checked_at < _SHARES_RECHECK_SECONDS:
        return False
    _shares_checked_at = now
    files = _prober().slskd_shared_files({'slskd_url': url,
                                           'api_key': config_manager.get('soulseek.api_key', '')})
    if files:
        config_manager.set('setup.checklist_done', [*list(done), 'slskd_shares'])
        return True
    return False


@bp.route('/api/onboarding/checklist', methods=['GET'])
@admin_only
def onboarding_checklist():
    shared = False
    if checklist.uses_soulseek(config_manager.get):
        try:
            shared = _slskd_shares_now()
        except Exception as exc:  # noqa: BLE001 - slskd being down is not the card's problem
            logger.debug("slskd share check failed: %s", exc)
    return jsonify(checklist.build(config_manager.get, profile_count=_profile_count(),
                                   tmdb_key=_tmdb_key(), slskd_shared=shared))


@bp.route('/api/onboarding/checklist', methods=['POST'])
@admin_only
def onboarding_checklist_update():
    body = _body()
    if 'dismissed' in body:
        config_manager.set('setup.checklist_dismissed', bool(body.get('dismissed')))
    item = str(body.get('done') or '')
    if item:
        done = config_manager.get('setup.checklist_done', []) or []
        done = list(done) if isinstance(done, list) else []
        if item not in done:
            done.append(item)
            config_manager.set('setup.checklist_done', done)
    return onboarding_checklist()


def create_blueprint():
    return bp
