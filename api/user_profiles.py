"""Multi-user profile endpoints, lifted out of web_server.py.

Profile CRUD, avatars, per-profile page permissions, per-profile service
credentials and the login/launch-PIN flow. The login limiters and the page-id
whitelist are web_server's (defined with its auth routes) and injected here."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request, session

from core.metadata import registry as metadata_registry
from core.metadata.status import invalidate_metadata_status_caches
from core.profile_context import admin_only

from utils.logging_config import get_logger

logger = get_logger("api.user_profiles")

bp = Blueprint("user_profiles", __name__)

# Injected by configure() at boot.
get_database = None
config_manager = None
get_current_profile_id = None
VALID_PAGE_IDS = None
_login_limiter = None
_launch_pin_limiter = None
_require_login_enabled = None



_get_metadata_fallback_source = None
clear_profile_tidal_client = None
_download_orchestrator = lambda: None   # noqa: E731 - rebindable boot global
_media_server_engine = lambda: None     # noqa: E731
_spotify_client = lambda: None          # noqa: E731 - rebound on reconnect


def configure(*, get_database, config_manager, get_current_profile_id, VALID_PAGE_IDS,
              _login_limiter, _launch_pin_limiter, _require_login_enabled,
              metadata_fallback_source, download_orchestrator_getter,
              media_server_engine_getter, spotify_client_getter, tidal_client_clearer):
    globals()['get_database'] = get_database
    globals()['config_manager'] = config_manager
    globals()['get_current_profile_id'] = get_current_profile_id
    globals()['VALID_PAGE_IDS'] = VALID_PAGE_IDS
    globals()['_login_limiter'] = _login_limiter
    globals()['_launch_pin_limiter'] = _launch_pin_limiter
    globals()['_require_login_enabled'] = _require_login_enabled
    globals()['_get_metadata_fallback_source'] = metadata_fallback_source
    globals()['_download_orchestrator'] = download_orchestrator_getter
    globals()['_media_server_engine'] = media_server_engine_getter
    globals()['_spotify_client'] = spotify_client_getter
    globals()['clear_profile_tidal_client'] = tidal_client_clearer


def create_blueprint():
    return bp


# --- Per-Profile ListenBrainz Settings ---

def _get_lb_credentials_for_profile(profile_id=None):
    """Get LB token + base_url for profile, falling back to global config."""
    if profile_id is None:
        profile_id = get_current_profile_id()
    db = get_database()
    settings = db.get_profile_listenbrainz(profile_id)
    if settings and settings.get('token'):
        return settings['token'], settings.get('base_url', ''), settings.get('username', ''), 'profile'
    # Fallback to global config
    return (config_manager.get('listenbrainz.token', ''),
            config_manager.get('listenbrainz.base_url', ''),
            None, 'global')

def _validate_lb_token(token, base_url=''):
    """Validate a ListenBrainz token and return (success, username_or_error)"""
    try:
        custom_base = (base_url or '').rstrip('/')
        if custom_base:
            if not custom_base.endswith('/1'):
                custom_base += '/1'
            lb_api_base = custom_base
        else:
            lb_api_base = "https://api.listenbrainz.org/1"
        url = f"{lb_api_base}/validate-token"
        headers = {'Authorization': f'Token {token}'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('valid'):
                return True, data.get('user_name', 'Unknown')
            return False, "Invalid ListenBrainz token."
        elif response.status_code == 401:
            return False, "Invalid ListenBrainz token (unauthorized)."
        else:
            return False, f"Could not connect to ListenBrainz (HTTP {response.status_code})"
    except Exception as e:
        return False, f"ListenBrainz connection error: {str(e)}"

# --- Per-Profile Service Credentials API ---

def _profile_spotify_connection(profile_id):
    """(connected, account_name) for a profile's OWN connected Spotify — not the
    global/admin fallback. A profile is connected only when its own token cache
    exists and authenticates."""
    if not profile_id or profile_id == 1:
        return (False, None)
    if not os.path.exists(f"config/.spotify_cache_profile_{profile_id}"):
        return (False, None)
    try:
        client = metadata_registry.get_spotify_client_for_profile(profile_id)
        if client and client.is_spotify_authenticated():
            info = client.get_user_info() or {}
            return (True, info.get('display_name') or info.get('id'))
    except Exception as e:
        logger.debug("profile %s spotify connection check failed: %s", profile_id, e)
    return (False, None)


def _profile_tidal_connection(profile_id):
    """(connected, account_name) for a profile's OWN Tidal. Connected = it has
    stored tokens; validity is checked (and refreshed) on actual use, so this
    stays a cheap no-network check for the modal."""
    if not profile_id or profile_id == 1:
        return (False, None)
    try:
        toks = get_database().get_profile_tidal(profile_id) or {}
        return (bool(toks.get('refresh_token') or toks.get('access_token')), None)
    except Exception as e:
        logger.debug("profile %s tidal connection check failed: %s", profile_id, e)
        return (False, None)


def _profile_listenbrainz_connection(profile_id):
    """(connected, username) for a profile's OWN ListenBrainz token."""
    if not profile_id or profile_id == 1:
        return (False, None)
    try:
        s = get_database().get_profile_listenbrainz(profile_id) or {}
        if s.get('token'):
            return (True, s.get('username'))
    except Exception as e:
        logger.debug("profile %s listenbrainz connection check failed: %s", profile_id, e)
    return (False, None)


def _disconnect_profile_spotify(pid):
    cache_path = f"config/.spotify_cache_profile_{pid}"
    try:
        if os.path.exists(cache_path):
            os.remove(cache_path)
    except Exception as e:
        logger.debug("could not remove profile spotify cache: %s", e)
    try:
        get_database().set_profile_spotify_tokens(pid, '', '')
    except Exception as e:
        logger.debug("could not clear profile spotify tokens: %s", e)
    metadata_registry.clear_cached_profile_spotify_client(pid)


def _disconnect_profile_tidal(pid):
    try:
        get_database().set_profile_tidal_tokens(pid, '', '')
    except Exception as e:
        logger.debug("could not clear profile tidal tokens: %s", e)
    clear_profile_tidal_client(pid)


def _disconnect_profile_listenbrainz(pid):
    try:
        get_database().clear_profile_listenbrainz(pid)
    except Exception as e:
        logger.debug("could not clear profile listenbrainz: %s", e)


_PROFILE_DISCONNECTORS = {
    'spotify': _disconnect_profile_spotify,
    'tidal': _disconnect_profile_tidal,
    'listenbrainz': _disconnect_profile_listenbrainz,
}


# ==================================================================================
# SERVICE CREDENTIAL SETS  (admin-created named "pills" per auth service; #profiles)
# ----------------------------------------------------------------------------------
# Admin manages the named credential sets; any profile only SELECTS among them
# (see /api/credentials/active + /select below). Payloads (the actual secrets)
# are NEVER returned to the browser — only id/service/label.
# ==================================================================================

@bp.route('/api/credentials', methods=['GET'])
@admin_only
def list_service_credentials_endpoint():
    """List all credential sets grouped by service (metadata only, no secrets)."""
    try:
        from core.credentials.store import SERVICE_CREDENTIAL_SCHEMA
        rows = get_database().list_service_credentials()
        grouped = {svc: [] for svc in SERVICE_CREDENTIAL_SCHEMA}
        for r in rows:
            grouped.setdefault(r['service'], []).append(
                {'id': r['id'], 'label': r['label'], 'updated_at': r['updated_at']}
            )
        return jsonify({'success': True, 'services': grouped})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/credentials', methods=['POST'])
@admin_only
def create_service_credential_endpoint():
    """Create a named credential set for a service. Body: {service, label, payload}."""
    try:
        from core.credentials.store import is_supported_service, validate_credential_payload
        data = request.json or {}
        service = (data.get('service') or '').strip()
        label = (data.get('label') or '').strip()
        payload = data.get('payload') or {}

        if not is_supported_service(service):
            return jsonify({'success': False, 'error': f'Unsupported service: {service}'}), 400
        if not label:
            return jsonify({'success': False, 'error': 'A name is required'}), 400
        ok, missing = validate_credential_payload(service, payload)
        if not ok:
            return jsonify({'success': False, 'error': f'Missing required fields: {", ".join(missing)}'}), 400

        cred_id = get_database().create_service_credential(service, label, payload)
        if cred_id is None:
            return jsonify({'success': False, 'error': f'A "{label}" set already exists for {service}'}), 409
        return jsonify({'success': True, 'id': cred_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/credentials/<int:credential_id>', methods=['PUT'])
@admin_only
def update_service_credential_endpoint(credential_id):
    """Update a credential set's label and/or payload. Only provided fields change."""
    try:
        from core.credentials.store import validate_credential_payload
        data = request.json or {}
        label = data.get('label')
        payload = data.get('payload')  # None = leave secrets untouched

        existing = get_database().get_service_credential(credential_id)
        if not existing:
            return jsonify({'success': False, 'error': 'Credential set not found'}), 404

        if label is not None and not str(label).strip():
            return jsonify({'success': False, 'error': 'Name cannot be empty'}), 400
        if payload is not None:
            ok, missing = validate_credential_payload(existing['service'], payload)
            if not ok:
                return jsonify({'success': False, 'error': f'Missing required fields: {", ".join(missing)}'}), 400

        updated = get_database().update_service_credential(
            credential_id,
            label=str(label).strip() if label is not None else None,
            payload=payload,
        )
        if not updated:
            return jsonify({'success': False, 'error': 'Nothing to update or name already in use'}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/credentials/<int:credential_id>', methods=['DELETE'])
@admin_only
def delete_service_credential_endpoint(credential_id):
    """Delete a credential set. Any profile that had it selected falls back to
    the global/admin default automatically (selection is cleared in the DB)."""
    try:
        ok = get_database().delete_service_credential(credential_id)
        if not ok:
            return jsonify({'success': False, 'error': 'Credential set not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================================================================================
# QUICK-SWITCH: active metadata source / media server / download source
# ----------------------------------------------------------------------------------
# The read is open to any profile (so the sidebar modal renders). Setting is
# admin-only and writes the GLOBAL config (same as the Settings page) — the
# per-profile override for non-admins is a separate, later layer. `editable`
# tells the UI whether to allow changes for the current profile.
# ==================================================================================

# Selectable metadata sources (mirrors the Settings <select>).
def _qs_metadata_sources():
    """Metadata sources offered in Connections / quick-switch UI."""
    from core.metadata.registry import EXPERIMENTAL_SOURCES, is_source_enabled
    sources = ['spotify', 'spotify_free', 'itunes', 'deezer', 'discogs', 'musicbrainz']
    sources += [name for name in EXPERIMENTAL_SOURCES if is_source_enabled(name)]
    return sources
_QS_MEDIA_SERVERS = ['plex', 'jellyfin', 'navidrome', 'soulsync']
# Single download sources (everything the mode accepts except 'hybrid').
_QS_DOWNLOAD_SOURCES = ['soulseek', 'youtube', 'tidal', 'qobuz', 'hifi', 'torrent', 'usenet']


def _qs_metadata_available(source):
    try:
        if source == 'spotify':
            return bool(_spotify_client() and _spotify_client().is_spotify_authenticated())
        if source == 'discogs':
            return bool(config_manager.get('discogs.token'))
        return True
    except Exception:
        return True


def _qs_server_available(server):
    try:
        if server == 'soulsync':
            return True
        if server == 'plex':
            return bool(config_manager.get('plex.base_url') or config_manager.get('plex.token'))
        return bool(config_manager.get(f'{server}.base_url'))
    except Exception:
        return True




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
        password = data.get('password', '')

        if not profile_id:
            return jsonify({'success': False, 'error': 'profile_id required'}), 400

        database = get_database()
        profile = database.get_profile(profile_id)
        if not profile:
            return jsonify({'success': False, 'error': 'Profile not found'}), 404

        if _require_login_enabled() and session.get('profile_id') != profile_id:
            _ip = request.remote_addr or 'unknown'
            _now = time.time()
            _locked, _retry_after = _login_limiter.is_locked(_ip, _now)
            if _locked:
                return (jsonify({'success': False, 'error': 'Too many attempts - please wait and try again'}),
                        429, {'Retry-After': str(_retry_after)})
            if not password:
                return jsonify({'success': False, 'error': 'Password required',
                                'password_required': True}), 401
            if not database.verify_profile_password(profile_id, password):
                _login_limiter.record_failure(_ip, _now)
                return jsonify({'success': False, 'error': 'Invalid password'}), 401
            _login_limiter.record_success(_ip)
        else:
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

@bp.route('/api/profiles/me/listenbrainz', methods=['GET'])
def get_profile_listenbrainz():
    """Get current profile's ListenBrainz connection status"""
    try:
        profile_id = get_current_profile_id()
        token, base_url, username, source = _get_lb_credentials_for_profile(profile_id)
        connected = bool(token)
        return jsonify({
            'success': True,
            'connected': connected,
            'username': username if connected else None,
            'base_url': base_url or '',
            'source': source
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/listenbrainz', methods=['POST'])
def save_profile_listenbrainz():
    """Save ListenBrainz credentials for current profile"""
    try:
        data = request.json or {}
        token = data.get('token', '').strip()
        base_url = data.get('base_url', '').strip()

        if not token:
            return jsonify({'success': False, 'error': 'Token is required'}), 400

        # Validate token first
        valid, result = _validate_lb_token(token, base_url)
        if not valid:
            return jsonify({'success': False, 'error': result}), 400

        username = result
        profile_id = get_current_profile_id()
        db = get_database()
        success = db.set_profile_listenbrainz(profile_id, token, base_url, username)

        if success:
            return jsonify({'success': True, 'username': username})
        return jsonify({'success': False, 'error': 'Failed to save credentials'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/listenbrainz', methods=['DELETE'])
def delete_profile_listenbrainz():
    """Clear ListenBrainz credentials for current profile"""
    try:
        profile_id = get_current_profile_id()
        db = get_database()
        db.clear_profile_listenbrainz(profile_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/listenbrainz/test', methods=['POST'])
def test_profile_listenbrainz():
    """Test a ListenBrainz token without saving"""
    try:
        data = request.json or {}
        token = data.get('token', '').strip()
        base_url = data.get('base_url', '').strip()

        if not token:
            return jsonify({'success': False, 'error': 'Token is required'}), 400

        valid, result = _validate_lb_token(token, base_url)
        if valid:
            return jsonify({'success': True, 'username': result})
        return jsonify({'success': False, 'error': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/connections', methods=['GET'])
def get_my_connections():
    """Per-profile playlist-service connection status for the My Accounts modal.
    Readable by any profile; reports only this profile's own connections."""
    try:
        pid = get_current_profile_id()
        sp_connected, sp_account = _profile_spotify_connection(pid)
        td_connected, td_account = _profile_tidal_connection(pid)
        lb_connected, lb_account = _profile_listenbrainz_connection(pid)
        return jsonify({
            'success': True,
            'is_admin': pid == 1,
            'connections': {
                'spotify': {'connected': sp_connected, 'account': sp_account},
                'tidal': {'connected': td_connected, 'account': td_account},
                'listenbrainz': {'connected': lb_connected, 'account': lb_account},
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/me/connections/<service>/disconnect', methods=['POST'])
def disconnect_my_connection(service):
    """Disconnect the current profile's OWN account for a service (clears its
    per-profile tokens + cached client). The global/admin auth is untouched."""
    try:
        pid = get_current_profile_id()
        fn = _PROFILE_DISCONNECTORS.get(service)
        if not fn:
            return jsonify({'success': False, 'error': f'Unsupported service: {service}'}), 400
        if pid == 1:
            return jsonify({'success': False, 'error': 'The admin account is managed in Settings'}), 400
        fn(pid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/me/spotify', methods=['GET'])
def get_profile_spotify_creds():
    """Get current profile's Spotify credentials (if set)"""
    try:
        profile_id = get_current_profile_id()
        db = get_database()
        creds = db.get_profile_spotify(profile_id)
        return jsonify({
            'success': True,
            'has_credentials': bool(creds),
            'client_id': creds.get('client_id', '') if creds else '',
            'redirect_uri': creds.get('redirect_uri', '') if creds else '',
            # Never return client_secret or tokens to frontend
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/spotify', methods=['POST'])
def save_profile_spotify_creds():
    """Save Spotify API credentials for current profile"""
    try:
        data = request.json or {}
        client_id = data.get('client_id', '').strip()
        client_secret = data.get('client_secret', '').strip()
        redirect_uri = data.get('redirect_uri', '').strip()

        if not client_id or not client_secret:
            return jsonify({'success': False, 'error': 'Client ID and Secret are required'}), 400

        profile_id = get_current_profile_id()
        db = get_database()
        success = db.set_profile_spotify(profile_id, client_id, client_secret, redirect_uri)

        if success:
            metadata_registry.clear_cached_profile_spotify_client(profile_id)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to save credentials'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/spotify', methods=['DELETE'])
def delete_profile_spotify_creds():
    """Clear Spotify credentials for current profile (revert to global)"""
    try:
        profile_id = get_current_profile_id()
        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE profiles
                SET spotify_client_id = NULL, spotify_client_secret = NULL,
                    spotify_redirect_uri = NULL, spotify_access_token = NULL,
                    spotify_refresh_token = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (profile_id,))
            conn.commit()
        metadata_registry.clear_cached_profile_spotify_client(profile_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/server-library', methods=['GET'])
def get_profile_server_library():
    """Get current profile's media server library selection"""
    try:
        profile_id = get_current_profile_id()
        db = get_database()
        libs = db.get_profile_server_library(profile_id)
        return jsonify({'success': True, **libs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/profiles/me/server-library', methods=['POST'])
def save_profile_server_library():
    """Save media server library/user selection for current profile"""
    try:
        data = request.json or {}
        server_type = data.get('server_type', '')
        library_id = data.get('library_id')
        user_id = data.get('user_id')

        if server_type not in ('plex', 'jellyfin', 'navidrome'):
            return jsonify({'success': False, 'error': 'Invalid server type'}), 400

        profile_id = get_current_profile_id()
        db = get_database()
        success = db.set_profile_server_library(profile_id, server_type, library_id, user_id)

        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to save library selection'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/me/services', methods=['GET'])
def get_my_service_selections():
    """For the current profile: the available credential sets per service (id +
    label, never secrets) and which one this profile has selected. Drives the
    quick-switch modal's pills. Any profile may read this — it exposes no
    secrets, only the admin-created set names. Stale-safe: a selection whose set
    was deleted reports as None (fall back to the global/admin default)."""
    try:
        from core.credentials.store import SERVICE_CREDENTIAL_SCHEMA
        db = get_database()
        profile_id = get_current_profile_id()
        out = {}
        for service in SERVICE_CREDENTIAL_SCHEMA:
            options = db.list_service_credentials(service)
            selected = db.get_profile_service_credential_id(profile_id, service)
            if selected not in {o['id'] for o in options}:
                selected = None
            out[service] = {
                'options': [{'id': o['id'], 'label': o['label']} for o in options],
                'selected_id': selected,
            }
        return jsonify({'success': True, 'services': out})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/me/services/select', methods=['POST'])
def select_my_service_credential():
    """Set which admin-created credential set is active for the current profile
    on a service. ``credential_id=null`` clears it (fall back to the global/admin
    default). The caller can only pick an EXISTING set for that service — never
    create one — so a non-admin can switch their account but not configure new
    credentials. Not admin-gated by design: it only writes a per-profile pointer
    and exposes no secrets."""
    try:
        from core.credentials.store import is_supported_service
        data = request.json or {}
        service = (data.get('service') or '').strip()
        credential_id = data.get('credential_id')

        if not is_supported_service(service):
            return jsonify({'success': False, 'error': f'Unsupported service: {service}'}), 400

        db = get_database()
        if credential_id is not None:
            cred = db.get_service_credential(credential_id)
            if not cred or cred['service'] != service:
                return jsonify({'success': False, 'error': 'No such credential set for this service'}), 400

        ok = db.set_profile_service_credential(get_current_profile_id(), service, credential_id)
        if ok:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to save selection'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/me/active-sources', methods=['GET'])
def get_active_sources():
    """Current active metadata source / media server / download source + the
    available options, for the quick-switch modal. Readable by any profile;
    reflects the global config (per-profile override is a later layer)."""
    try:
        mode = config_manager.get('download_source.mode', 'soulseek') or 'soulseek'
        hybrid_order = config_manager.get('download_source.hybrid_order', []) or []
        # "Spotify (no auth)" is a COMPOSITE the Settings page uses: it stores
        # fallback_source='spotify' + metadata.spotify_free=true, NOT a literal
        # 'spotify_free' fallback value. Mirror that mapping so the modal agrees
        # with the Settings dropdown (settings.js _metaSel / save logic).
        _fb = config_manager.get('metadata.fallback_source', 'deezer') or 'deezer'
        _free = config_manager.get('metadata.spotify_free', False)
        meta_active = 'spotify_free' if (_fb == 'spotify' and _free) else _fb
        meta_effective = 'spotify_free' if meta_active == 'spotify_free' else _get_metadata_fallback_source()
        return jsonify({
            'success': True,
            'editable': get_current_profile_id() == 1,  # admin writes the global default
            'metadata': {
                # `active` = the configured choice (what the user picked / edits).
                # `effective` = what's actually used after auth/availability
                # fallback (e.g. configured 'spotify' but not authenticated →
                # the app falls back). Surfacing both stops the modal disagreeing
                # with the sidebar/Settings status.
                'active': meta_active,
                'effective': meta_effective,
                'options': [{'id': s, 'available': _qs_metadata_available(s)} for s in _qs_metadata_sources()],
            },
            'server': {
                'active': config_manager.get_active_media_server(),
                'options': [{'id': s, 'available': _qs_server_available(s)} for s in _QS_MEDIA_SERVERS],
            },
            'download': {
                'mode': mode,
                'hybrid_order': hybrid_order,
                'options': [{'id': s} for s in _QS_DOWNLOAD_SOURCES],
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/profiles/active-sources', methods=['POST'])
@admin_only
def set_active_sources():
    """Set the GLOBAL active metadata source / media server / download mode +
    hybrid order (whichever fields are present). Admin-only; reuses the same
    setters + client reloads the Settings save performs so changes take effect
    immediately."""
    try:
        data = request.json or {}
        changed = []

        if 'metadata_source' in data:
            src = data['metadata_source']
            if src not in _qs_metadata_sources():
                return jsonify({'success': False, 'error': 'Unknown metadata source'}), 400
            from core.metadata.registry import apply_primary_metadata_source
            _primary_err = apply_primary_metadata_source(
                src,
                config_manager.set,
            )
            if _primary_err:
                return jsonify({'success': False, 'error': _primary_err}), 400
            invalidate_metadata_status_caches()
            changed.append('metadata')

        if 'media_server' in data:
            srv = data['media_server']
            if srv not in _QS_MEDIA_SERVERS:
                return jsonify({'success': False, 'error': 'Unknown media server'}), 400
            config_manager.set_active_media_server(srv)
            for s in ('plex', 'jellyfin', 'navidrome'):
                c = _media_server_engine().client(s)
                if c:
                    if s == 'plex':
                        c.server = None
                    else:
                        c.reload_config()
            changed.append('server')

        if 'download_mode' in data:
            mode = data['download_mode']
            if mode not in (_QS_DOWNLOAD_SOURCES + ['hybrid']):
                return jsonify({'success': False, 'error': 'Unknown download mode'}), 400
            config_manager.set('download_source.mode', mode)
            changed.append('download')

        if 'hybrid_order' in data and isinstance(data['hybrid_order'], list):
            clean = [s for s in data['hybrid_order'] if s in _QS_DOWNLOAD_SOURCES]
            config_manager.set('download_source.hybrid_order', clean)
            changed.append('download')

        if 'download' in changed and _download_orchestrator():
            _download_orchestrator().reload_settings()

        return jsonify({'success': True, 'changed': sorted(set(changed))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


