"""the person's own musicbrainz account, for isrc submissions (#2335).

musicbrainz takes isrcs from an app only under a person's account, through
oauth2 (https://musicbrainz.org/doc/Development/OAuth2). soulsync runs on
the person's own server at an address musicbrainz can't know, so it uses the
installed application flow: musicbrainz shows the person a code after they
approve, and they paste it into Settings (redirect ``urn:ietf:wg:oauth:2.0:oob``).
pkce (S256) guards the code. scopes: ``submit_isrc`` to send isrcs,
``profile`` to show whose account is linked.

TODO(maintainer): register soulsync once at
https://musicbrainz.org/account/applications as an "Installed application"
with the callback ``urn:ietf:wg:oauth:2.0:oob``, then fill in
``SOULSYNC_MB_CLIENT_ID`` and ``SOULSYNC_MB_CLIENT_SECRET`` below.
musicbrainz says an installed app's secret isn't treated as secret, so it
ships in the code like the acoustid application key. until then the env
vars ``MUSICBRAINZ_OAUTH_CLIENT_ID`` / ``MUSICBRAINZ_OAUTH_CLIENT_SECRET``,
or the giveback.musicbrainz_client_id / _secret settings, let someone use an
application they registered themselves.

the tokens live in the encrypted setting ``giveback.musicbrainz_auth`` and
never leave the server.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from core.giveback import common
from utils.logging_config import get_logger

logger = get_logger("giveback.musicbrainz_oauth")

# TODO(maintainer): soulsync's registered musicbrainz application (see above)
SOULSYNC_MB_CLIENT_ID = ''
SOULSYNC_MB_CLIENT_SECRET = ''

CLIENT_ID_ENV = 'MUSICBRAINZ_OAUTH_CLIENT_ID'
CLIENT_SECRET_ENV = 'MUSICBRAINZ_OAUTH_CLIENT_SECRET'
CLIENT_ID_SETTING = 'giveback.musicbrainz_client_id'
CLIENT_SECRET_SETTING = 'giveback.musicbrainz_client_secret'
AUTH_SETTING = 'giveback.musicbrainz_auth'
PENDING_SETTING = 'giveback.musicbrainz_pending'
USER_SETTING = 'giveback.musicbrainz_user'

SITE = 'https://musicbrainz.org'
AUTHORIZE_URL = f'{SITE}/oauth2/authorize'
TOKEN_URL = f'{SITE}/oauth2/token'
REVOKE_URL = f'{SITE}/oauth2/revoke'
USERINFO_URL = f'{SITE}/oauth2/userinfo'
REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'
SCOPES = 'submit_isrc profile'
TIMEOUT = 20
# a started link the person never finished is forgotten after this
PENDING_TTL = 3600

UNAVAILABLE_MESSAGE = ("Sending ISRCs needs SoulSync registered with MusicBrainz, which "
                       "arrives in a coming update.")


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def _get(cfg, key: str, default: Any = '') -> Any:
    try:
        return common.cfg_of(cfg).get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _set(cfg, key: str, value: Any) -> None:
    common.cfg_of(cfg).set(key, value)


def client(cfg=None) -> Dict[str, str]:
    """the registered application: built in, then env, then settings"""
    cid = _s(SOULSYNC_MB_CLIENT_ID) or _s(os.environ.get(CLIENT_ID_ENV)) or _s(_get(cfg, CLIENT_ID_SETTING))
    secret = (_s(SOULSYNC_MB_CLIENT_SECRET) or _s(os.environ.get(CLIENT_SECRET_ENV))
              or _s(_get(cfg, CLIENT_SECRET_SETTING)))
    return {'id': cid, 'secret': secret}


def available(cfg=None) -> bool:
    return bool(client(cfg)['id'])


def _http():
    import requests
    return requests


def _headers() -> Dict[str, str]:
    return {'User-Agent': common.user_agent(), 'Accept': 'application/json'}


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def start(cfg=None, *, now: Optional[float] = None) -> Dict[str, Any]:
    """a fresh link: answers {'url'} to open, or {'error'}"""
    app = client(cfg)
    if not app['id']:
        return {'error': UNAVAILABLE_MESSAGE}
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    _set(cfg, PENDING_SETTING, {'verifier': verifier, 'state': state,
                                'created': float(now if now is not None else time.time())})
    query = urlencode({
        'response_type': 'code',
        'client_id': app['id'],
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPES,
        'state': state,
        'code_challenge': _challenge(verifier),
        'code_challenge_method': 'S256',
    })
    return {'url': f'{AUTHORIZE_URL}?{query}'}


def _save_tokens(cfg, data: Dict[str, Any], old: Dict[str, Any], now: float) -> Dict[str, Any]:
    try:
        expires_in = float(data.get('expires_in') or 3600)
    except (TypeError, ValueError):
        expires_in = 3600.0
    auth = {
        'access_token': _s(data.get('access_token')),
        # a refresh answer may leave the refresh token out: keep the old one
        'refresh_token': _s(data.get('refresh_token')) or _s(old.get('refresh_token')),
        'expires_at': now + expires_in,
    }
    _set(cfg, AUTH_SETTING, auth)
    return auth


def finish(code: str, cfg=None, *, http=None, now: Optional[float] = None) -> Dict[str, Any]:
    """trade the code the person pasted for tokens. answers {'ok': True,
    'username'} or {'error'}"""
    code = _s(code)
    if not code:
        return {'error': 'Paste the code MusicBrainz showed you'}
    app = client(cfg)
    if not app['id']:
        return {'error': UNAVAILABLE_MESSAGE}
    now = float(now if now is not None else time.time())
    pending = _get(cfg, PENDING_SETTING, {}) or {}
    if not isinstance(pending, dict) or not pending.get('verifier') \
            or now - float(pending.get('created') or 0) > PENDING_TTL:
        return {'error': 'That link ran out. Press Connect again'}
    http = http or _http()
    form = {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': app['id'],
        'client_secret': app['secret'],
        'redirect_uri': REDIRECT_URI,
        'code_verifier': pending['verifier'],
    }
    try:
        resp = http.post(TOKEN_URL, data=form, headers=_headers(), timeout=TIMEOUT)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("MusicBrainz token exchange failed: %s", type(e).__name__)
        return {'error': 'Could not reach MusicBrainz'}
    if not isinstance(data, dict) or not data.get('access_token'):
        return {'error': 'MusicBrainz did not take that code. Press Connect and try again'}
    _set(cfg, PENDING_SETTING, {})
    auth = _save_tokens(cfg, data, {}, now)
    username = whoami(auth['access_token'], http=http)
    _set(cfg, USER_SETTING, username)
    logger.info("MusicBrainz account linked for give back")
    return {'ok': True, 'username': username}


def whoami(token: str, *, http=None) -> str:
    http = http or _http()
    try:
        resp = http.get(USERINFO_URL, headers={**_headers(), 'Authorization': f'Bearer {token}'},
                        timeout=TIMEOUT)
        data = resp.json()
    except Exception:  # noqa: BLE001 - the name is only for show
        return ''
    return _s(data.get('sub')) if isinstance(data, dict) else ''


def access_token(cfg=None, *, http=None, now: Optional[float] = None) -> str:
    """a working access token, refreshed when it is about to run out; '' when
    there is no link or musicbrainz refused the refresh"""
    auth = _get(cfg, AUTH_SETTING, {}) or {}
    if not isinstance(auth, dict) or not auth.get('refresh_token'):
        return ''
    now = float(now if now is not None else time.time())
    if auth.get('access_token') and float(auth.get('expires_at') or 0) - 60 > now:
        return auth['access_token']
    app = client(cfg)
    if not app['id']:
        return ''
    http = http or _http()
    form = {
        'grant_type': 'refresh_token',
        'refresh_token': auth['refresh_token'],
        'client_id': app['id'],
        'client_secret': app['secret'],
    }
    try:
        resp = http.post(TOKEN_URL, data=form, headers=_headers(), timeout=TIMEOUT)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("MusicBrainz token refresh failed: %s", type(e).__name__)
        return ''
    if not isinstance(data, dict) or not data.get('access_token'):
        logger.warning("MusicBrainz refused the token refresh; link the account again")
        return ''
    return _save_tokens(cfg, data, auth, now)['access_token']


def disconnect(cfg=None, *, http=None) -> None:
    """forget the link here, and tell musicbrainz to drop it too"""
    auth = _get(cfg, AUTH_SETTING, {}) or {}
    token = _s(auth.get('refresh_token')) if isinstance(auth, dict) else ''
    app = client(cfg)
    if token and app['id']:
        http = http or _http()
        try:
            http.post(REVOKE_URL, data={'token': token, 'token_type_hint': 'refresh_token',
                                        'client_id': app['id'], 'client_secret': app['secret']},
                      headers=_headers(), timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001 - forgotten here either way
            logger.debug("MusicBrainz revoke failed: %s", e)
    _set(cfg, AUTH_SETTING, {})
    _set(cfg, PENDING_SETTING, {})
    _set(cfg, USER_SETTING, '')


def status(cfg=None) -> Dict[str, Any]:
    """what the settings card shows. never carries a token"""
    auth = _get(cfg, AUTH_SETTING, {}) or {}
    connected = isinstance(auth, dict) and bool(auth.get('refresh_token'))
    return {
        'available': available(cfg),
        'connected': connected,
        'username': _s(_get(cfg, USER_SETTING, '')) if connected else '',
        'message': '' if available(cfg) else UNAVAILABLE_MESSAGE,
    }
