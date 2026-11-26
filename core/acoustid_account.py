"""acoustid keys: soulsync's application key and the user's own key (#2318).

from 1 january 2027 a lookup from a registered application needs two keys:
the application's (``client``) and the person's own (``user``). without the
user key acoustid answers error 21. this module holds both keys, the linking
flow (request_link, then link_status until the person approves on
acoustid.org) and the lookup call that sends both.

the application key is soulsync's own, registered once by the maintainer, so
nobody has to register an application of their own any more. until it is
filled in, an ``ACOUSTID_APP_KEY`` env var or the old per-install
"application API key" setting stands in, so nothing breaks today.

no network or config work happens at import.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("acoustid.account")

# soulsync's registered acoustid application key. the maintainer registers
# one application at acoustid.org/new-application and puts its key here.
# empty means "not registered yet": the env var or the old setting is used.
SOULSYNC_ACOUSTID_APP_KEY = ''

APP_KEY_ENV = 'ACOUSTID_APP_KEY'
APP_KEY_SETTING = 'acoustid.api_key'
USER_KEY_SETTING = 'acoustid.user_key'
LINK_SETTING = 'acoustid.link'

API_BASE = 'https://api.acoustid.org/v2'
LINK_PAGE = 'https://acoustid.org/link'
USER_KEY_PAGE = 'https://acoustid.org/api-key'

# lookups need the user's key from this day on
USER_KEY_REQUIRED_FROM = date(2027, 1, 1)

# acoustid-server error codes
ACOUSTID_ERROR_INVALID_APIKEY = 4
ACOUSTID_ERROR_USER_KEY_REQUIRED = 21

NEEDS_LINK_MESSAGE = ("AcoustID needs your account linked: Settings, Connections, AcoustID, "
                      "Link your AcoustID account (or paste your user API key there).")
LINK_PENDING_MESSAGE = ("AcoustID has not approved your linked account yet: finish linking in "
                        "Settings, Connections, AcoustID.")

LINK_STATES = ('pending', 'approved', 'declined', 'expired', 'revoked', 'unknown')
# the key is dead in these states: acoustid says discard it
DISCARD_STATES = ('declined', 'expired', 'revoked', 'unknown')

TIMEOUT = 15
_MIN_INTERVAL = 1.0 / 3  # acoustid allows 3 requests a second
_rate_lock = threading.Lock()
_last_call = [0.0]


def _cfg(cfg=None):
    if cfg is not None:
        return cfg
    from core.settings import config_manager
    return config_manager


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def builtin_app_key() -> str:
    """soulsync's own key: the env override, else the built-in constant."""
    return _str(os.environ.get(APP_KEY_ENV)) or _str(SOULSYNC_ACOUSTID_APP_KEY)


def has_builtin_app_key() -> bool:
    return bool(builtin_app_key())


def app_key(cfg=None) -> str:
    """the application key every call sends as ``client``: soulsync's own,
    else the application key this install registered itself."""
    own = builtin_app_key()
    if own:
        return own
    try:
        return _str(_cfg(cfg).get(APP_KEY_SETTING, ''))
    except Exception:  # noqa: BLE001 - no config means no key
        return ''


def user_key(cfg=None) -> str:
    try:
        return _str(_cfg(cfg).get(USER_KEY_SETTING, ''))
    except Exception:  # noqa: BLE001
        return ''


def user_key_required(today: Optional[date] = None) -> bool:
    return (today or date.today()) >= USER_KEY_REQUIRED_FROM


def missing_user_key(cfg=None, today: Optional[date] = None) -> bool:
    """true when a lookup can't work: the cutoff has passed and no user key."""
    return user_key_required(today) and not user_key(cfg)


def key_tag(key: str) -> str:
    """a short hash, so a stored link can tell whether it is still the key in
    the settings without keeping the key twice"""
    return hashlib.sha256((key or '').encode('utf-8')).hexdigest()[:16] if key else ''


def headers(user: str = '') -> Dict[str, str]:
    """the user's key goes in a header, not the url: acoustid recommends it,
    as a url ends up in logs and history."""
    return {'Acoustid-User-Key': user} if user else {}


def _wait_turn():
    with _rate_lock:
        gap = time.monotonic() - _last_call[0]
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_call[0] = time.monotonic()


def _call(method: str, path: str, params: Dict[str, Any], app: str, user: str = '',
          timeout: float = TIMEOUT) -> Dict[str, Any]:
    """one acoustid web service call. always answers a dict: acoustid's json,
    or {'status': 'error', 'error': {'code': 0, 'message': ...}} when the
    network or the body failed."""
    import requests
    _wait_turn()
    params = dict(params, format='json', client=app)
    try:
        if method == 'POST':
            resp = requests.post(f'{API_BASE}/{path}', data=params,
                                 headers=headers(user), timeout=timeout)
        else:
            resp = requests.get(f'{API_BASE}/{path}', params=params,
                                headers=headers(user), timeout=timeout)
    except requests.exceptions.Timeout:
        return {'status': 'error', 'error': {'code': 0, 'message': 'AcoustID timed out'}}
    except requests.exceptions.RequestException as e:
        return {'status': 'error', 'error': {'code': 0, 'message': f'could not reach AcoustID: {type(e).__name__}'}}
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {'status': 'error', 'error': {'code': 0, 'message': f'AcoustID answered HTTP {resp.status_code}'}}
    return data


def error_of(data: Dict[str, Any]) -> Tuple[int, str]:
    err = data.get('error') if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return 0, 'Unknown AcoustID error'
    try:
        code = int(err.get('code') or 0)
    except (TypeError, ValueError):
        code = 0
    return code, str(err.get('message') or 'Unknown AcoustID error')


# ── linking ──

def _link(cfg) -> Dict[str, Any]:
    try:
        rec = _cfg(cfg).get(LINK_SETTING, {})
    except Exception:  # noqa: BLE001
        rec = {}
    return dict(rec) if isinstance(rec, dict) else {}


def _save_link(cfg, rec: Dict[str, Any]) -> None:
    _cfg(cfg).set(LINK_SETTING, rec)


def _clear_user_key(cfg) -> None:
    c = _cfg(cfg)
    clear = getattr(c, 'clear_secret', None)
    if callable(clear):
        clear(USER_KEY_SETTING)
    else:
        c.set(USER_KEY_SETTING, '')


def link_pending(cfg=None) -> bool:
    """the stored user key came from a link acoustid hasn't approved yet"""
    rec = _link(cfg)
    key = user_key(cfg)
    return bool(key) and rec.get('state') == 'pending' and rec.get('key_tag') == key_tag(key)


def request_link(cfg=None, device: str = '', return_url: str = '') -> Dict[str, Any]:
    """start linking: acoustid hands back a user key (stored at once, it works
    once the person approves), a short code and a url to show them."""
    cfg = _cfg(cfg)
    app = app_key(cfg)
    if not app:
        return {'ok': False, 'error': 'No AcoustID application key: SoulSync has none built in yet, '
                                      'so add your own application key first.'}
    params: Dict[str, Any] = {}
    device = _str(device)[:100]
    if device:
        params['device'] = device
    return_url = _str(return_url)
    if return_url:
        if not return_url.lower().startswith(('http://', 'https://')) or len(return_url) > 2000:
            return {'ok': False, 'error': 'return_url must be an http or https address'}
        params['return_url'] = return_url
    data = _call('POST', 'user/request_link', params, app)
    if data.get('status') != 'ok':
        code, message = error_of(data)
        logger.warning("AcoustID request_link failed (code %s): %s", code, message)
        return {'ok': False, 'error': f'AcoustID refused the link request: {message}', 'code': code}
    user = data.get('user') if isinstance(data.get('user'), dict) else {}
    link = user.get('link') if isinstance(user.get('link'), dict) else {}
    key = _str(user.get('apikey'))
    if not key:
        return {'ok': False, 'error': 'AcoustID answered without a user key'}
    code = _str(link.get('code'))
    url = _str(link.get('url')) or (f'{LINK_PAGE}?code={code}' if code else LINK_PAGE)
    # stored right away: acoustid says to keep it, it starts working on approval
    cfg.set(USER_KEY_SETTING, key)
    rec = {'state': 'pending', 'code': code, 'url': url, 'expires': _str(link.get('expires')),
           'key_tag': key_tag(key), 'app_tag': key_tag(app),
           'requested_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    _save_link(cfg, rec)
    logger.info("AcoustID link requested, waiting for approval (code %s)", code)
    return {'ok': True, **_public(rec)}


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: rec.get(k, '') for k in ('state', 'code', 'url', 'expires')}


def _expired(expires: str) -> bool:
    if not expires:
        return False
    try:
        when = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= when


def check_link(cfg=None) -> Dict[str, Any]:
    """ask acoustid about a pending link and settle it. a declined, expired,
    revoked or unknown key is discarded, as acoustid asks; an approved one
    stays. only the key the link stored is ever discarded: a key pasted since
    is the person's and is left alone."""
    cfg = _cfg(cfg)
    rec = _link(cfg)
    key = user_key(cfg)
    if rec.get('state') != 'pending':
        return _public(rec) if rec else {'state': ''}
    if not key or rec.get('key_tag') != key_tag(key):
        # the key changed under the link (pasted or cleared): the link is moot
        _save_link(cfg, {})
        return {'state': ''}
    app = app_key(cfg)
    data = _call('GET', 'user/link_status', {}, app, key)
    if data.get('status') != 'ok':
        code, message = error_of(data)
        logger.warning("AcoustID link_status failed (code %s): %s", code, message)
        if _expired(rec.get('expires', '')):
            state = 'expired'
        else:
            return {**_public(rec), 'error': message}
    else:
        state_data = data.get('key') if isinstance(data.get('key'), dict) else {}
        state = _str(state_data.get('state')) or 'unknown'
        if state not in LINK_STATES:
            state = 'unknown'
        if state == 'pending' and _expired(rec.get('expires', '')):
            state = 'expired'
    if state == 'pending':
        return _public(rec)
    rec['state'] = state
    if state in DISCARD_STATES:
        _clear_user_key(cfg)
        rec['key_tag'] = ''
        logger.warning("AcoustID link %s: the linked key was discarded", state)
    else:
        logger.info("AcoustID account linked")
    _save_link(cfg, rec)
    return _public(rec)


def unlink(cfg=None) -> None:
    cfg = _cfg(cfg)
    _clear_user_key(cfg)
    _save_link(cfg, {})


def status(cfg=None, today: Optional[date] = None) -> Dict[str, Any]:
    """what the settings card shows. never carries a key."""
    cfg = _cfg(cfg)
    rec = _link(cfg)
    key = user_key(cfg)
    linked_here = bool(key) and rec.get('key_tag') == key_tag(key) and bool(rec.get('key_tag'))
    state = rec.get('state', '') if (linked_here or rec.get('state') in DISCARD_STATES) else ''
    if key and not linked_here:
        source = 'pasted'
    elif key:
        source = 'linked'
    else:
        source = ''
    needs = missing_user_key(cfg, today)
    if needs:
        message = NEEDS_LINK_MESSAGE
    elif key and state == 'pending':
        message = LINK_PENDING_MESSAGE
    else:
        message = ''
    return {
        'has_user_key': bool(key),
        'source': source,
        'state': state,
        'code': rec.get('code', '') if state == 'pending' else '',
        'url': rec.get('url', '') if state == 'pending' else '',
        'expires': rec.get('expires', '') if state == 'pending' else '',
        'has_app_key': bool(app_key(cfg)),
        'builtin_app_key': has_builtin_app_key(),
        'required_from': USER_KEY_REQUIRED_FROM.isoformat(),
        'required_now': user_key_required(today),
        'needs_link': needs,
        'message': message,
    }


# ── lookups ──

def lookup(fingerprint: str, duration: float, app: str, user: str = '',
           meta: str = 'recordings', timeout: float = TIMEOUT) -> Dict[str, Any]:
    """/v2/lookup with the application key and the user's key, the same
    form post pyacoustid makes. answers acoustid's json (status ok or error)."""
    params = {'duration': int(duration), 'fingerprint': fingerprint, 'meta': meta}
    return _call('POST', 'lookup', params, app, user, timeout=timeout)


def explain(code: int, message: str, cfg=None) -> Dict[str, Any]:
    """turn an acoustid error into what a person can act on.

    {'needs_user_key': bool, 'invalid_key': bool, 'message': str}. error 21
    and a linked key still waiting for approval (acoustid rejects it with the
    invalid-key error until then) both point at the link, not at a broken key.
    """
    if code == ACOUSTID_ERROR_USER_KEY_REQUIRED:
        return {'needs_user_key': True, 'invalid_key': False, 'message': NEEDS_LINK_MESSAGE}
    if code == ACOUSTID_ERROR_INVALID_APIKEY and link_pending(cfg):
        return {'needs_user_key': True, 'invalid_key': False, 'message': LINK_PENDING_MESSAGE}
    return {'needs_user_key': False, 'invalid_key': False, 'message': message}


_last_needs_log = [0.0]


def log_needs_link(message: str = NEEDS_LINK_MESSAGE) -> None:
    """an error in the log, at most once every ten minutes: a scan would
    otherwise write it once per file"""
    now = time.monotonic()
    if _last_needs_log[0] and now - _last_needs_log[0] < 600:
        return
    _last_needs_log[0] = now
    logger.error(message)
