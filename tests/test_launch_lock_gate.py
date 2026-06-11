"""Server-side launch-PIN gate (#832, Beckid).

The admin PIN was a client-side overlay only; removing the div gave full API
access. request_is_locked() is the pure allow/deny decision the before_request
gate uses. These pin the exact matrix so the lock can't silently regress to
'advisory'.
"""

from __future__ import annotations

import pytest

from core.security.launch_lock import request_is_locked


def L(path, method='GET', require_pin=True, pin_verified=False):
    return request_is_locked(path, method, require_pin=require_pin, pin_verified=pin_verified)


# ── gate disabled / already verified → never locks ──────────────────────────

def test_no_lock_when_require_pin_off():
    # The default config: nothing is gated.
    assert L('/api/downloads/start', 'POST', require_pin=False) is False
    assert L('/api/settings', 'POST', require_pin=False) is False


def test_no_lock_when_session_verified():
    assert L('/api/downloads/start', 'POST', pin_verified=True) is False
    assert L('/api/v1/api-keys-internal/generate', 'POST', pin_verified=True) is False


# ── locked session blocks the app ───────────────────────────────────────────

@pytest.mark.parametrize('path,method', [
    ('/api/watchlist/artists', 'GET'),
    ('/api/downloads/start', 'POST'),
    ('/api/settings', 'POST'),
    ('/api/wishlist/tracks', 'GET'),
    ('/api/library/artists', 'GET'),
    ('/socket.io/', 'GET'),
])
def test_locked_blocks_data_and_action_endpoints(path, method):
    assert L(path, method) is True


def test_locked_blocks_profile_mutation():
    # Creating/editing/deleting profiles or setting PINs must NOT be reachable
    # pre-auth — else an attacker mints an admin or rewrites the PIN.
    assert L('/api/profiles', 'POST') is True        # create
    assert L('/api/profiles/2', 'PUT') is True        # edit
    assert L('/api/profiles/2', 'DELETE') is True     # delete
    assert L('/api/profiles/1/set-pin', 'POST') is True


def test_locked_blocks_internal_key_minting():
    # The documented "no auth required" key-management endpoints are the
    # secondary bypass: mint a key, walk in via the public API. Must be locked.
    assert L('/api/v1/api-keys-internal', 'GET') is True
    assert L('/api/v1/api-keys-internal/generate', 'POST') is True
    assert L('/api/v1/api-keys-internal/revoke/abc', 'DELETE') is True


# ── locked session still allows the unlock flow + shell ─────────────────────

@pytest.mark.parametrize('path', ['/', '/static/init.js', '/static/dist/app.js', '/favicon.ico'])
def test_locked_allows_page_shell_and_assets(path):
    assert L(path) is False


def test_locked_allows_unlock_flow():
    assert L('/api/profiles/current', 'GET') is False
    assert L('/api/profiles', 'GET') is False                              # picker list
    assert L('/api/profiles/select', 'POST') is False
    assert L('/api/profiles/verify-launch-pin', 'POST') is False
    assert L('/api/profiles/reset-pin-via-credential', 'POST') is False
    assert L('/api/profiles/logout', 'POST') is False


def test_locked_allows_setup_status():
    # #842: the first-run check runs before the PIN screen. Blocking it made the
    # frontend think setup was incomplete and relaunch the wizard every visit.
    assert L('/api/setup/status', 'GET') is False


def test_locked_allows_keyauthed_public_api():
    # The public REST API carries its own @require_api_key, so a launch-locked
    # UI must not break a legitimate headless key holder.
    assert L('/api/v1/search', 'GET') is False
    assert L('/api/v1/playlists', 'GET') is False


def test_method_matters_for_shared_paths():
    # GET /api/profiles is the picker (allowed); POST /api/profiles is create
    # (blocked). Same path, opposite verdicts.
    assert L('/api/profiles', 'GET') is False
    assert L('/api/profiles', 'POST') is True


# ── blocked navigations bounce to the lock screen, not JSON (#832 follow-up) ──

from core.security.launch_lock import is_html_navigation  # noqa: E402


def test_browser_navigation_is_detected():
    # Address-bar / link / refresh — gets redirected to the root lock screen.
    assert is_html_navigation('GET', 'text/html,application/xhtml+xml,*/*', '') is True
    assert is_html_navigation('GET', '*/*', 'navigate') is True


def test_programmatic_fetch_is_not_navigation():
    # fetch()/XHR want JSON so the frontend can react to the 401.
    assert is_html_navigation('GET', '*/*', 'cors') is False
    assert is_html_navigation('GET', 'application/json', 'same-origin') is False
    assert is_html_navigation('GET', '', '') is False


def test_non_get_is_never_navigation():
    # A programmatic POST/DELETE always gets JSON, never a redirect.
    assert is_html_navigation('POST', 'text/html', 'navigate') is False
    assert is_html_navigation('DELETE', 'text/html', '') is False
