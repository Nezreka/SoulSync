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

from core.security.rate_limit import AttemptLimiter
from utils.logging_config import get_logger

logger = get_logger("api.login")

# Brute-force limiter for /api/auth/login and the recovery reset (lenient;
# only a flood of wrong answers from one IP trips it - success clears it).
login_limiter = AttemptLimiter(max_attempts=10, window_seconds=300)

# injected by configure()
get_database = None


def configure(*, get_database_):
    global get_database
    get_database = get_database_


bp = Blueprint('login', __name__)


@bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Username/password login (opt-in login mode). Username = profile name.
    Brute-force limited per IP; a profile with no password set can't log in."""
    try:
        _ip = request.remote_addr or 'unknown'
        _now = time.time()
        _locked, _retry_after = login_limiter.is_locked(_ip, _now)
        if _locked:
            return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                    429, {'Retry-After': str(_retry_after)})

        data = request.json or {}
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password required'}), 400

        database = get_database()
        profile = database.get_profile_by_name(username)
        # Same generic error + a recorded failure whether the name or password is
        # wrong — don't leak which names exist.
        if not profile or not database.verify_profile_password(profile['id'], password):
            login_limiter.record_failure(_ip, _now)
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

        login_limiter.record_success(_ip)
        session['login_authenticated'] = True
        session['profile_id'] = profile['id']
        # A fresh login also clears any stale launch-PIN flag.
        session.pop('launch_pin_verified', None)
        return jsonify({'success': True, 'profile': {
            'id': profile['id'], 'name': profile['name'], 'is_admin': profile['is_admin'],
        }})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Log out — clears the authenticated session."""
    try:
        session.pop('login_authenticated', None)
        session.pop('profile_id', None)
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
        _locked, _retry_after = login_limiter.is_locked(_ip, _now)
        if _locked:
            return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                    429, {'Retry-After': str(_retry_after)})

        data = request.json or {}
        username = (data.get('username') or '').strip()
        answer = data.get('answer') or ''
        new_password = data.get('new_password') or ''
        if not username or not answer or not new_password:
            return jsonify({'success': False, 'error': 'Username, answer and new password are required'}), 400
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'New password must be at least 6 characters'}), 400

        database = get_database()
        profile = database.get_profile_by_name(username)
        if not profile or not database.verify_profile_recovery_answer(profile['id'], answer):
            login_limiter.record_failure(_ip, _now)
            return jsonify({'success': False, 'error': 'Incorrect answer'}), 401

        login_limiter.record_success(_ip)
        database.set_profile_password(profile['id'], new_password)
        session['login_authenticated'] = True
        session['profile_id'] = profile['id']
        session.pop('launch_pin_verified', None)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def create_blueprint() -> Blueprint:
    return bp
