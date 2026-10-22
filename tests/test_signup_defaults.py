"""What a profile made by sign-in starts with: the security.signup_* settings.

The helper is pure; the plex half runs sign_in against the test database with
plex.tv never called. Nothing set must give exactly what sign-up made before
these settings existed: music pages, NULL sides, no request limit.
"""

from __future__ import annotations

import json
import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-signupdefaults-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.security import plex_signin  # noqa: E402
from core.security.plex_signin import PlexAccount  # noqa: E402
from core.security.signup_defaults import (  # noqa: E402
    SIGNUP_PAGES,
    SIGNUP_VIDEO_PAGES,
    SignupAccess,
    signup_access,
)


def _get(values):
    return lambda key, default=None: values.get(key, default)


def _pages(profile):
    pages = profile['allowed_pages']
    return json.loads(pages) if isinstance(pages, str) else pages


def _account():
    return PlexAccount(str(int(uuid4().int % 10**9)), f'Signup{uuid4().hex[:8]}', 'tok')


# -- the helper --

def test_nothing_set_is_what_sign_up_always_made():
    for access in (signup_access(), signup_access(_get({}))):
        assert access == SignupAccess()
        assert access.allowed_sides is None
        assert access.allowed_pages == SIGNUP_PAGES
        assert access.home_page == 'discover'
        assert access.request_limit == 0


def test_music_is_stored_as_the_default_not_spelled_out():
    assert signup_access(_get({'security.signup_sides': 'music'})).allowed_sides is None


def test_both_sides_opens_the_music_and_the_video_pages():
    a = signup_access(_get({'security.signup_sides': 'both'}))
    assert a.allowed_sides == 'both'
    assert a.allowed_pages == SIGNUP_PAGES + SIGNUP_VIDEO_PAGES
    assert a.home_page == 'discover'


def test_video_only_opens_the_video_pages_and_starts_there():
    a = signup_access(_get({'security.signup_sides': 'video'}))
    assert a.allowed_sides == 'video'
    assert set(a.allowed_pages) == {'help', 'issues', *SIGNUP_VIDEO_PAGES}
    assert a.home_page == 'video-discover'


def test_an_unknown_side_falls_back_to_music():
    for junk in ('everything', '', None, 3, ['both']):
        assert signup_access(_get({'security.signup_sides': junk})).allowed_sides is None


def test_the_request_limit_is_held_to_the_editors_bounds():
    def limit(v, d=7):
        a = signup_access(_get({'security.signup_request_limit': v, 'security.signup_request_limit_days': d}))
        return a.request_limit, a.request_limit_days

    assert limit(5) == (5, 7)
    assert limit('12', '30') == (12, 30)
    assert limit(-3) == (0, 7)
    assert limit(5000) == (1000, 7)
    assert limit('lots') == (0, 7)
    assert limit(5, 0) == (5, 1)
    assert limit(5, 9999) == (5, 365)
    assert limit(5, None) == (5, 7)


def test_the_signup_pages_are_all_real_and_none_is_machinery():
    for pages in (SIGNUP_PAGES, SIGNUP_VIDEO_PAGES):
        assert set(pages) <= web_server.VALID_PAGE_IDS
    for machinery in ('settings', 'sync', 'automations', 'import', 'active-downloads', 'tools',
                      'video-downloads', 'video-wishlist', 'video-tools'):
        assert machinery not in SIGNUP_PAGES + SIGNUP_VIDEO_PAGES


def test_plex_signin_still_offers_the_page_names():
    assert plex_signin.SIGNUP_PAGES is SIGNUP_PAGES
    assert plex_signin.SIGNUP_HOME_PAGE == 'discover'


# -- plex sign-up uses it --

def test_plex_sign_up_without_settings_is_unchanged():
    db = web_server.get_database()
    r = plex_signin.sign_in(db, _account(), allow_create=True, default_can_download=False)
    p = db.get_profile(r.profile_id)
    assert r.created and set(_pages(p)) == set(SIGNUP_PAGES)
    assert p['allowed_sides'] == 'music'      # NULL resolves to music for a member
    assert p['request_limit'] == 0 and p['request_limit_days'] == 7


def test_plex_sign_up_gets_the_side_pages_and_limit_the_admin_set():
    db = web_server.get_database()
    access = signup_access(_get({'security.signup_sides': 'both', 'security.signup_request_limit': 4,
                                 'security.signup_request_limit_days': 14}))
    r = plex_signin.sign_in(db, _account(), allow_create=True, default_can_download=False, access=access)
    p = db.get_profile(r.profile_id)
    assert p['allowed_sides'] == 'both'
    assert set(_pages(p)) == set(SIGNUP_PAGES + SIGNUP_VIDEO_PAGES)
    assert p['request_limit'] == 4 and p['request_limit_days'] == 14
    assert not p['is_admin'] and not p['can_download']


def test_the_access_only_applies_to_new_profiles():
    db = web_server.get_database()
    acct = _account()
    first = plex_signin.sign_in(db, acct, allow_create=True, default_can_download=False)
    again = plex_signin.sign_in(db, acct, allow_create=True, default_can_download=False,
                                access=signup_access(_get({'security.signup_sides': 'both',
                                                           'security.signup_request_limit': 2})))
    assert again.profile_id == first.profile_id and not again.created
    p = db.get_profile(first.profile_id)
    assert p['allowed_sides'] == 'music' and p['request_limit'] == 0


def test_the_owner_is_still_the_admin_whatever_the_settings():
    db = web_server.get_database()
    r = plex_signin.sign_in(db, PlexAccount('9', 'owner', 'srv', owns_server=True),
                            allow_create=True, default_can_download=False,
                            access=signup_access(_get({'security.signup_sides': 'video'})))
    assert r.profile_id == 1


# -- the settings round trip --

def test_the_settings_save_and_the_route_reads_them(monkeypatch):
    from core.settings import config_manager
    r = web_server.app.test_client().post('/api/settings', json={'security': {
        'signup_sides': 'both', 'signup_request_limit': 3, 'signup_request_limit_days': 10,
    }})
    assert r.status_code == 200, r.get_data(as_text=True)
    try:
        assert config_manager.get('security.signup_sides') == 'both'
        a = signup_access(config_manager.get)
        assert a.allowed_sides == 'both' and a.request_limit == 3 and a.request_limit_days == 10
    finally:
        config_manager.set('security.signup_sides', 'music')
        config_manager.set('security.signup_request_limit', 0)
        config_manager.set('security.signup_request_limit_days', 7)


def test_the_plex_route_passes_the_settings_to_sign_up(monkeypatch):
    seen = {}
    real = plex_signin.sign_in

    def spy(db, account, **kw):
        seen.update(kw)
        return real(db, account, **kw)

    values = {'security.require_login': True, 'security.plex_signin': True,
              'security.plex_signin_auto_create': True, 'security.plex_signin_default_can_download': False,
              'security.signup_sides': 'video', 'security.signup_request_limit': 6}
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: values[k] if k in values else real_get(k, d))
    import api.login as login_api
    web_server._login_limiter.reset()
    login_api.plex_start_limiter.reset()
    monkeypatch.setattr(login_api, '_PLEX_CHECK_MIN_INTERVAL', 0)
    monkeypatch.setattr(plex_signin, 'client_identifier', lambda cm: 'soulsync-test')
    monkeypatch.setattr(plex_signin, 'start_pin', lambda cid, forward_url='': {'id': 77, 'url': 'https://x'})
    monkeypatch.setattr(plex_signin, 'check_pin', lambda cid, pin_id: 'acct-token')
    monkeypatch.setattr(plex_signin, 'server_machine_id', lambda plex: 'machine-1')
    monkeypatch.setattr(plex_signin, 'resolve_account', lambda token, machine: _account())
    monkeypatch.setattr(plex_signin, 'sign_in', spy)

    c = web_server.app.test_client()
    assert c.post('/api/auth/plex/start').status_code == 200
    body = c.post('/api/auth/plex/check').get_json()
    assert body['success'] and body['created'], body
    assert seen['access'].allowed_sides == 'video' and seen['access'].request_limit == 6
    p = web_server.get_database().get_profile(body['profile']['id'])
    assert p['allowed_sides'] == 'video' and p['request_limit'] == 6
