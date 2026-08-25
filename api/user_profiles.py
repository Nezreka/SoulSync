"""Multi-user profile endpoints, lifted out of web_server.py.

Profile CRUD, avatars, per-profile settings and the active-profile switch.
Pure config/DB surface - the three injected deps are web_server's stable boot
singletons."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from utils.logging_config import get_logger

logger = get_logger("api.user_profiles")

bp = Blueprint("user_profiles", __name__)

# Injected by configure() at boot.
get_database = None
config_manager = None
get_current_profile_id = None



# Auth plumbing shared with web_server's login routes (injected).
VALID_PAGE_IDS = None
_login_limiter = None
_launch_pin_limiter = None
_require_login_enabled = None


def configure(*, get_database, config_manager, get_current_profile_id,
              valid_page_ids, login_limiter, launch_pin_limiter, require_login_enabled):
    globals()['get_database'] = get_database
    globals()['config_manager'] = config_manager
    globals()['get_current_profile_id'] = get_current_profile_id
    globals()['VALID_PAGE_IDS'] = valid_page_ids
    globals()['_login_limiter'] = login_limiter
    globals()['_launch_pin_limiter'] = launch_pin_limiter
    globals()['_require_login_enabled'] = require_login_enabled


def create_blueprint():
    return bp


# --- Profile API Endpoints ---

@bp.route('/api/profiles', methods=['GET'])
def list_profiles():
    """List all profiles"""
    try:
        database = get_database()
        profiles = database.get_all_profiles()
        return jsonify({'success': True, 'profiles': profiles})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles', methods=['POST'])
def create_profile():
    """Create a new profile (admin only)"""
    try:
        # Check that requester is admin
        database = get_database()
        current = database.get_profile(get_current_profile_id())
        if current and not current['is_admin']:
            return jsonify({'success': False, 'error': 'Admin only'}), 403

        data = request.json or {}
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400

        avatar_color = data.get('avatar_color', '#6366f1')
        avatar_url = data.get('avatar_url') or None
        pin = data.get('pin')
        pin_hash = None
        if pin:
            from werkzeug.security import generate_password_hash
            pin_hash = generate_password_hash(pin, method='pbkdf2:sha256')

        # No-gaps: while login mode is on, a new member must be born with a login
        # password or they could never sign in.
        password = (data.get('password') or '').strip()
        from core.security.login_provisioning import create_needs_password
        if create_needs_password(_require_login_enabled()) and not password:
            return jsonify({'success': False,
                            'error': 'Login mode is on — give this profile a login '
                                     'password so they can sign in.'}), 400

        # Profile settings: home_page, allowed_pages, can_download, allowed_sides
        home_page = data.get('home_page') or None
        allowed_pages = data.get('allowed_pages')  # list or None
        can_download = data.get('can_download', True)
        # Side access — music | video | both, never nothing. Anything else
        # falls back to the shipped default (music-only for non-admins).
        allowed_sides = data.get('allowed_sides')
        if allowed_sides not in ('music', 'video', 'both'):
            allowed_sides = None

        # Validate page IDs
        if home_page and home_page not in VALID_PAGE_IDS:
            home_page = None
        if allowed_pages is not None:
            allowed_pages = [p for p in allowed_pages if p in VALID_PAGE_IDS]
            # Non-admin should never have 'settings' in allowed_pages
            if 'settings' in allowed_pages:
                allowed_pages.remove('settings')
            # If home_page not in allowed list, reset to first allowed or 'discover'
            if home_page and home_page not in allowed_pages:
                home_page = allowed_pages[0] if allowed_pages else None

        profile_id = database.create_profile(
            name, avatar_color, pin_hash, is_admin=False, avatar_url=avatar_url,
            home_page=home_page, allowed_pages=allowed_pages, can_download=bool(can_download),
            allowed_sides=allowed_sides
        )
        if profile_id is None:
            return jsonify({'success': False, 'error': 'Profile name already exists'}), 409

        if password:
            database.set_profile_password(profile_id, password)

        return jsonify({'success': True, 'profile_id': profile_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/<int:profile_id>', methods=['PUT'])
def update_profile(profile_id):
    """Update a profile (admin or self)"""
    try:
        database = get_database()
        current_pid = get_current_profile_id()
        current = database.get_profile(current_pid)
        if not current:
            return jsonify({'success': False, 'error': 'Current profile not found'}), 404

        # Only admin or self can update
        if not current['is_admin'] and current_pid != profile_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        data = request.json or {}
        kwargs = {}
        if 'name' in data:
            name = data['name'].strip()
            if not name:
                return jsonify({'success': False, 'error': 'Name cannot be empty'}), 400
            kwargs['name'] = name
        if 'avatar_color' in data:
            kwargs['avatar_color'] = data['avatar_color']
        if 'avatar_url' in data:
            kwargs['avatar_url'] = data['avatar_url'] or None
        if 'is_admin' in data and current['is_admin']:
            # Prevent demoting the last admin
            if not data['is_admin']:
                all_profiles = database.get_all_profiles()
                admin_count = sum(1 for p in all_profiles if p['is_admin'])
                target = database.get_profile(profile_id)
                if target and target['is_admin'] and admin_count <= 1:
                    return jsonify({'success': False, 'error': 'Cannot remove the last admin'}), 400
            kwargs['is_admin'] = int(data['is_admin'])

        # Home page — any user can change their own, admin can change anyone's
        if 'home_page' in data:
            hp = data['home_page'] or None
            if hp and hp not in VALID_PAGE_IDS:
                hp = None
            # Non-admin self-edit: validate home_page is in their allowed pages
            if not current['is_admin'] and current_pid == profile_id:
                target = database.get_profile(profile_id)
                ap = target.get('allowed_pages') if target else None
                if ap is not None and hp and hp not in ap:
                    return jsonify({'success': False, 'error': 'Page not permitted'}), 400
            kwargs['home_page'] = hp

        # Allowed pages & can_download — admin only
        if current['is_admin']:
            if 'allowed_pages' in data:
                ap = data['allowed_pages']
                if ap is not None:
                    ap = [p for p in ap if p in VALID_PAGE_IDS]
                    # Non-admin target should never have 'settings'
                    target = database.get_profile(profile_id)
                    if target and not target.get('is_admin'):
                        ap = [p for p in ap if p != 'settings']
                    # If current home_page not in new allowed list, reset it
                    current_hp = kwargs.get('home_page') or (target.get('home_page') if target else None)
                    if current_hp and current_hp not in ap:
                        kwargs['home_page'] = ap[0] if ap else None
                kwargs['allowed_pages'] = ap
            if 'can_download' in data:
                kwargs['can_download'] = int(bool(data['can_download']))
            if 'allowed_sides' in data:
                # music | video | both, never nothing — invalid values reset to
                # NULL (the shipped music-only default for non-admins). Stored
                # values on ADMIN profiles are inert: the read side always
                # resolves admins to 'both'.
                sides = data['allowed_sides']
                kwargs['allowed_sides'] = sides if sides in ('music', 'video', 'both') else None

        success = database.update_profile(profile_id, **kwargs)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/<int:profile_id>', methods=['DELETE'])
def delete_profile(profile_id):
    """Delete a profile (admin only, can't delete self)"""
    try:
        database = get_database()
        current_pid = get_current_profile_id()
        current = database.get_profile(current_pid)

        if not current or not current['is_admin']:
            return jsonify({'success': False, 'error': 'Admin only'}), 403
        if current_pid == profile_id:
            return jsonify({'success': False, 'error': 'Cannot delete your own profile'}), 400

        target = database.get_profile(profile_id)
        if not target:
            return jsonify({'success': False, 'error': 'Profile not found'}), 404

        success = database.delete_profile(profile_id)
        if success:
            from api.profiles import _sweep_video_profile_data
            _sweep_video_profile_data(profile_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/select', methods=['POST'])
def select_profile():
    """Select a profile (validates PIN if set)"""
    try:
        data = request.json or {}
        try:
            profile_id = int(data.get('profile_id', 0))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid profile_id'}), 400
        pin = data.get('pin', '')

        if not profile_id:
            return jsonify({'success': False, 'error': 'profile_id required'}), 400

        database = get_database()
        profile = database.get_profile(profile_id)
        if not profile:
            return jsonify({'success': False, 'error': 'Profile not found'}), 404

        # Only enforce PIN when multiple profiles exist (PIN protects against profile switching)
        all_profiles = database.get_all_profiles()
        if len(all_profiles) > 1 and profile['has_pin']:
            if not pin:
                return jsonify({'success': False, 'error': 'PIN required', 'pin_required': True}), 401
            if not database.verify_profile_pin(profile_id, pin):
                return jsonify({'success': False, 'error': 'Invalid PIN'}), 401

        session['profile_id'] = profile_id
        # If the admin PIN was just validated, also mark launch PIN as
        # verified so the subsequent page reload doesn't ask again. A
        # non-admin profile PIN must not unlock the admin launch lock.
        if pin and profile_id == 1:
            session['launch_pin_verified'] = True
        return jsonify({'success': True, 'profile': profile})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/current', methods=['GET'])
def get_current_profile():
    """Get the currently selected profile from session"""
    try:
        # Login mode: when on and the session isn't authenticated, tell the
        # frontend to show the sign-in screen (this is checked before profile
        # selection, since there's no profile until you log in).
        if _require_login_enabled() and not session.get('login_authenticated', False):
            return jsonify({'success': False, 'login_required': True}), 200

        pid = session.get('profile_id')
        if not pid:
            return jsonify({'success': False, 'error': 'No profile selected'}), 200

        database = get_database()
        profile = database.get_profile(pid)
        if not profile:
            session.pop('profile_id', None)
            return jsonify({'success': False, 'error': 'Profile not found'}), 200

        # Check if launch PIN is required
        require_pin = config_manager.get('security.require_pin_on_launch', False) if config_manager else False
        # #832: READ (don't pop) the verified flag — the server-side gate
        # (_enforce_launch_pin) relies on it persisting for the whole session,
        # so verified requests keep passing. Verification now lasts the session
        # (until logout / cookie expiry) instead of one page load — which is
        # both what an enforced gate requires and the correct security model.
        pin_verified = session.get('launch_pin_verified', False)

        return jsonify({
            'success': True,
            'profile': profile,
            'launch_pin_required': bool(require_pin) and not pin_verified,
            'login_mode': _require_login_enabled(),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/verify-launch-pin', methods=['POST'])
def verify_launch_pin():
    """Verify PIN for launch lock screen"""
    try:
        # Brute-force guard: only a flood of WRONG PINs from one IP trips this; a
        # correct entry clears it instantly, so normal use is never affected.
        _ip = request.remote_addr or 'unknown'
        _now = time.time()
        _locked, _retry_after = _launch_pin_limiter.is_locked(_ip, _now)
        if _locked:
            return (jsonify({'success': False, 'error': 'Too many attempts — please wait and try again'}),
                    429, {'Retry-After': str(_retry_after)})

        data = request.json or {}
        pin = data.get('pin', '')
        if not pin:
            return jsonify({'success': False, 'error': 'PIN required'}), 401

        database = get_database()
        # Validate against admin profile (ID 1)
        if not database.verify_profile_pin(1, pin):
            _launch_pin_limiter.record_failure(_ip, _now)
            return jsonify({'success': False, 'error': 'Invalid PIN'}), 401

        _launch_pin_limiter.record_success(_ip)
        session['launch_pin_verified'] = True
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Username/password login (opt-in login mode). Username = profile name.
    Brute-force limited per IP; a profile with no password set can't log in."""
    try:
        _ip = request.remote_addr or 'unknown'
        _now = time.time()
        _locked, _retry_after = _login_limiter.is_locked(_ip, _now)
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
            _login_limiter.record_failure(_ip, _now)
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

        _login_limiter.record_success(_ip)
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
        _locked, _retry_after = _login_limiter.is_locked(_ip, _now)
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
            _login_limiter.record_failure(_ip, _now)
            return jsonify({'success': False, 'error': 'Incorrect answer'}), 401

        _login_limiter.record_success(_ip)
        database.set_profile_password(profile['id'], new_password)
        session['login_authenticated'] = True
        session['profile_id'] = profile['id']
        session.pop('launch_pin_verified', None)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/<int:profile_id>/set-recovery', methods=['POST'])
def set_profile_recovery_endpoint(profile_id):
    """Set or clear a profile's recovery question + answer (admin, or self)."""
    try:
        database = get_database()
        current_pid = get_current_profile_id()
        current = database.get_profile(current_pid)
        if not current or (not current['is_admin'] and current_pid != profile_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        data = request.json or {}
        ok = database.set_profile_recovery(profile_id, data.get('question', ''), data.get('answer', ''))
        return jsonify({'success': bool(ok), 'has_recovery': database.profile_has_recovery(profile_id)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/reset-pin-via-credential', methods=['POST'])
def reset_pin_via_credential():
    """Reset admin PIN by verifying a known API credential"""
    try:
        data = request.json or {}
        credential = (data.get('credential') or '').strip()
        if not credential or len(credential) < 4:
            return jsonify({'success': False, 'error': 'Enter a valid credential'}), 400

        # Check credential against all stored API secrets/tokens
        checks = [
            ('Spotify Client Secret',  config_manager.get('spotify.client_secret', '')),
            ('Tidal Client Secret',    config_manager.get('tidal.client_secret', '')),
            ('Plex Token',             config_manager.get('plex.token', '')),
            ('Jellyfin API Key',       config_manager.get('jellyfin.api_key', '')),
            ('Navidrome Password',     config_manager.get('navidrome.password', '')),
            ('ListenBrainz Token',     config_manager.get('listenbrainz.token', '')),
            ('AcoustID API Key',       config_manager.get('acoustid.api_key', '')),
            ('Last.fm API Secret',     config_manager.get('lastfm.api_secret', '')),
            ('Genius Access Token',    config_manager.get('genius.access_token', '')),
        ]

        matched = False
        for _name, stored in checks:
            if stored and credential == stored:
                matched = True
                break

        if not matched:
            return jsonify({'success': False, 'error': 'Credential does not match any configured service'}), 401

        # Credential verified — clear PIN for the requested profile (default: admin)
        database = get_database()
        try:
            target_profile = int(data.get('profile_id', 1))
        except (TypeError, ValueError):
            target_profile = 1
        database.update_profile(target_profile, pin_hash=None)
        # If clearing admin PIN, also disable launch lock
        if target_profile == 1:
            config_manager.set('security.require_pin_on_launch', False)
        if target_profile == 1:
            session['launch_pin_verified'] = True

        return jsonify({'success': True, 'message': 'PIN cleared. You can set a new PIN in Settings.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/logout', methods=['POST'])
def logout_profile():
    """Clear session — back to profile picker"""
    session.pop('profile_id', None)
    return jsonify({'success': True})

@bp.route('/api/profiles/<int:profile_id>/set-pin', methods=['POST'])
def set_profile_pin(profile_id):
    """Set or change PIN for a profile (admin or self)"""
    try:
        database = get_database()
        current_pid = get_current_profile_id()
        current = database.get_profile(current_pid)

        if not current or (not current['is_admin'] and current_pid != profile_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        data = request.json or {}
        pin = data.get('pin', '')

        if pin:
            from werkzeug.security import generate_password_hash
            pin_hash = generate_password_hash(pin, method='pbkdf2:sha256')
        else:
            pin_hash = None  # Remove PIN

        success = database.update_profile(profile_id, pin_hash=pin_hash)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/<int:profile_id>/set-password', methods=['POST'])
def set_profile_password_endpoint(profile_id):
    """Set or clear a profile's LOGIN password (admin, or the profile itself).
    Distinct from the quick-switch PIN."""
    try:
        database = get_database()
        current_pid = get_current_profile_id()
        current = database.get_profile(current_pid)
        if not current or (not current['is_admin'] and current_pid != profile_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        data = request.json or {}
        password = data.get('password', '')
        # No-gaps: clearing a password while login mode is on would lock that
        # profile out — refuse it (delete the profile instead if that's intended).
        from core.security.login_provisioning import removing_password_strands
        if not (password or '').strip() and removing_password_strands(_require_login_enabled()):
            return jsonify({'success': False,
                            'error': "Can't remove this password while login mode is on — "
                                     "that profile couldn't sign in."}), 400
        ok = database.set_profile_password(profile_id, password)
        return jsonify({'success': bool(ok), 'has_password': database.profile_has_password(profile_id)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

