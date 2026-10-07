"""Login/session endpoints - lifted from web_server.py.

username/password login for the opt-in login mode, logout, and the
recovery-question reset flow. named api/login.py because api/auth.py is
TAKEN - that one is the public REST API's key authentication, and the
first attempt at this lift overwrote it (caught by the routemap diff:
every /api/v1 route vanished).

the shared brute-force limiter lives here with its routes; web_server
imports it back to inject into api/user_profiles (its admin endpoints
clear lockouts). bodies byte-identical; only the decorator changed and
the limiter dropped its underscore on the way over.
"""

import time

from flask import Blueprint, jsonify, request, session

from core.security.rate_limit import TargetedLimiter
from utils.logging_config import get_logger

logger = get_logger("api.login")

# Brute-force limiter for /api/auth/login and the recovery reset (lenient;
# only a flood of wrong answers trips it). keyed by (ip, username): a success
# clears only the account that succeeded, so signing in as yourself no longer
# resets your guesses at someone else's.
login_limiter = TargetedLimiter(max_attempts=10, window_seconds=300)

# injected by configure()
get_database = None
config_manager = None
get_plex_client = None


def configure(*, get_database_, config_manager_=None, get_plex_client_=None):
    global get_database, config_manager, get_plex_client
    get_database = get_database_
    config_manager = config_manager_
    get_plex_client = get_plex_client_


def _complete_sign_in(database, profile_id):
    """the session a successful sign-in gets, password or plex alike"""
    session['login_authenticated'] = True
    session['profile_id'] = profile_id
    # the epoch this sign-in counts under ("sign out everywhere" moves it)
    session['profile_epoch'] = (database.get_profile(profile_id) or {}).get('session_epoch', 0)
    from core.security.devices import start_device
    start_device(session, database.add_profile_device, profile_id,
                 request.headers.get('User-Agent', ''), request.remote_addr or '')
    session['device_owner'] = profile_id
    # A fresh login also clears any stale launch-PIN flag.
    session.pop('launch_pin_verified', None)


bp = Blueprint('login', __name__)


@bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Username/password login (opt-in login mode). Username = profile name.
    Brute-force limited per IP; a profile with no password set can't log in."""
    try:
        _ip = request.remote_addr or 'unknown'
        _now = time.time()
        data = request.json or {}
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        _locked, _retry_after = login_limiter.is_locked(_ip, username, _now)
        if _locked:
            return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                    429, {'Retry-After': str(_retry_after)})
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password required'}), 400

        database = get_database()
        profile = database.get_profile_by_name(username)
        # Same generic error + a recorded failure whether the name or password is
        # wrong — don't leak which names exist. a turned-off profile reads the same.
        if profile and not profile.get('is_admin') and (database.get_profile(profile['id']) or {}).get('disabled'):
            profile = None
        if not profile or not database.verify_profile_password(profile['id'], password):
            login_limiter.record_failure(_ip, username, _now)
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

        login_limiter.record_success(_ip, username)
        _complete_sign_in(database, profile['id'])
        return jsonify({'success': True, 'profile': {
            'id': profile['id'], 'name': profile['name'], 'is_admin': profile['is_admin'],
        }})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _plex_signin_enabled():
    return bool(config_manager and config_manager.get('security.plex_signin', False))


@bp.route('/api/auth/plex/available', methods=['GET'])
def plex_signin_available():
    """whether the login screen offers sign in with plex"""
    return jsonify({'success': True, 'enabled': _plex_signin_enabled()})


@bp.route('/api/auth/plex/start', methods=['POST'])
def plex_signin_start():
    """a plex pin for this browser, and plex's page to approve it. the pin
    is remembered in this session only, so nobody else can claim it"""
    if not _plex_signin_enabled():
        return jsonify({'success': False, 'error': 'Plex sign-in is turned off'}), 403
    _ip = request.remote_addr or 'unknown'
    _now = time.time()
    _locked, _retry_after = login_limiter.is_locked(_ip, '<plex>', _now)
    if _locked:
        return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                429, {'Retry-After': str(_retry_after)})
    try:
        from core.security import plex_signin
        cid = plex_signin.client_identifier(config_manager)
        pin = plex_signin.start_pin(cid)
        session['plex_signin_pin'] = pin['id']
        return jsonify({'success': True, 'url': pin['url']})
    except Exception as e:
        logger.error(f"Plex sign-in could not start: {e}")
        login_limiter.record_failure(_ip, '<plex>', _now)
        return jsonify({'success': False, 'error': "Couldn't reach Plex. Try again in a moment."}), 502


@bp.route('/api/auth/plex/check', methods=['POST'])
def plex_signin_check():
    """poll the session's pin. pending until the user approves it on plex,
    then signed in (or told why not)"""
    if not _plex_signin_enabled():
        return jsonify({'success': False, 'error': 'Plex sign-in is turned off'}), 403
    pin_id = session.get('plex_signin_pin')
    if not pin_id:
        return jsonify({'success': False, 'error': 'Start Plex sign-in first'}), 400
    try:
        from core.security import plex_signin
        cid = plex_signin.client_identifier(config_manager)
        account_token = plex_signin.check_pin(cid, pin_id)
        if not account_token:
            return jsonify({'success': True, 'pending': True})
        session.pop('plex_signin_pin', None)
        plex = get_plex_client() if get_plex_client else None
        machine = plex_signin.server_machine_id(plex)
        if not machine:
            return jsonify({'success': False, 'error': "SoulSync isn't connected to a Plex server"}), 503
        account = plex_signin.resolve_account(account_token, machine)
        database = get_database()
        result = plex_signin.sign_in(
            database, account,
            owner_account_id=plex_signin.owner_account_id(plex),
            allow_create=bool(config_manager.get('security.plex_signin_auto_create', True)),
            default_can_download=bool(config_manager.get('security.plex_signin_default_can_download', False)),
        )
        if result.error:
            login_limiter.record_failure(request.remote_addr or 'unknown', '<plex>', time.time())
            return jsonify({'success': False, 'error': result.error}), 403
        _complete_sign_in(database, result.profile_id)
        profile = database.get_profile(result.profile_id) or {}
        return jsonify({'success': True, 'pending': False, 'created': result.created, 'profile': {
            'id': result.profile_id, 'name': profile.get('name'), 'is_admin': bool(profile.get('is_admin')),
        }})
    except Exception as e:
        logger.error(f"Plex sign-in failed: {e}")
        return jsonify({'success': False, 'error': "Plex sign-in failed. Try again."}), 502


@bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Log out — clears the authenticated session."""
    try:
        if session.get('device_id') and session.get('profile_id'):
            get_database().revoke_profile_device(session['profile_id'], session['device_id'])
        session.pop('login_authenticated', None)
        session.pop('profile_id', None)
        session.pop('profile_epoch', None)
        session.pop('device_id', None)
        session.pop('launch_pin_verified', None)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/auth/recovery-question', methods=['GET'])
def auth_recovery_question():
    """Return the recovery security-question for a username (forgot-password flow).
    Generic when the user/question is absent — don't confirm which names exist."""
    try:
        username = (request.args.get('username') or '').strip()
        database = get_database()
        profile = database.get_profile_by_name(username) if username else None
        question = database.get_profile_recovery_question(profile['id']) if profile else None
        if not question:
            return jsonify({'success': False, 'error': 'No recovery question available'}), 404
        return jsonify({'success': True, 'question': question})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/auth/recovery-reset', methods=['POST'])
def auth_recovery_reset():
    """Reset a login password by answering the recovery question. Brute-force
    limited; a correct answer sets the new password and authenticates the session."""
    try:
        _ip = request.remote_addr or 'unknown'
        _now = time.time()
        data = request.json or {}
        username = (data.get('username') or '').strip()
        _locked, _retry_after = login_limiter.is_locked(_ip, username, _now)
        if _locked:
            return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                    429, {'Retry-After': str(_retry_after)})
        answer = data.get('answer') or ''
        new_password = data.get('new_password') or ''
        if not username or not answer or not new_password:
            return jsonify({'success': False, 'error': 'Username, answer and new password are required'}), 400
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'New password must be at least 6 characters'}), 400

        database = get_database()
        profile = database.get_profile_by_name(username)
        if not profile or not database.verify_profile_recovery_answer(profile['id'], answer):
            login_limiter.record_failure(_ip, username, _now)
            return jsonify({'success': False, 'error': 'Incorrect answer'}), 401

        login_limiter.record_success(_ip, username)
        database.set_profile_password(profile['id'], new_password)
        # a reset password signs out every other browser on that profile
        database.bump_profile_session_epoch(profile['id'])
        from core.security.session_epoch import forget
        forget(profile['id'])
        session['login_authenticated'] = True
        session['profile_id'] = profile['id']
        # the epoch this sign-in counts under ("sign out everywhere" moves it)
        session['profile_epoch'] = (database.get_profile(profile['id']) or {}).get('session_epoch', 0)
        from core.security.devices import start_device
        start_device(session, database.add_profile_device, profile['id'],
                     request.headers.get('User-Agent', ''), request.remote_addr or '')
        session['device_owner'] = profile['id']
        session.pop('launch_pin_verified', None)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def create_blueprint() -> Blueprint:
    return bp
