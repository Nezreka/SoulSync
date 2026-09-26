"""profile / pin / issues security holes found in the sept 24 2026 review.

each test names the hole it pins. the pure rules are tested directly; the
routes run for real through the web_server app with a session set the way a
browser would have it.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-profsec-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'profsec.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core.issues.lifecycle import clean_new_issue, clean_update, visible_to  # noqa: E402
from core.permissions import (  # noqa: E402
    download_denied_reason, may_manage_profile, profile_can_download, profile_view_for,
)
from core.security.rate_limit import TargetedLimiter  # noqa: E402
from core.security.session_profile import (  # noqa: E402
    no_profile_request_is_blocked, resolve_session_profile,
)


# ── pure rules ───────────────────────────────────────────────────────────────

def test_no_session_profile_is_admin_only_on_a_single_profile_install():
    """logout / deleted profile / fresh browser used to read as profile 1."""
    assert resolve_session_profile(session_pid=None, login_mode=False, profile_count=1) == 1
    assert resolve_session_profile(session_pid=None, login_mode=False, profile_count=2) is None
    assert resolve_session_profile(session_pid=None, login_mode=True, profile_count=1) is None
    assert resolve_session_profile(session_pid=5, login_mode=True, profile_count=9) == 5
    assert resolve_session_profile(session_pid='bad', login_mode=False, profile_count=3) is None


def test_no_profile_gate_leaves_the_picker_and_page_shells_open():
    for path, method in (('/', 'GET'), ('/library', 'GET'), ('/static/x.js', 'GET'),
                         ('/api/profiles', 'GET'), ('/api/profiles/current', 'GET'),
                         ('/api/profiles/select', 'POST'), ('/api/auth/login', 'POST'),
                         ('/api/v1/library/artists', 'GET'), ('/callback', 'GET')):
        assert not no_profile_request_is_blocked(path, method), path
    for path, method in (('/api/issues', 'GET'), ('/api/profiles', 'POST'),
                         ('/api/v1/api-keys-internal/generate', 'POST'),
                         ('/stream/audio', 'GET'), ('/api/debug-info', 'GET')):
        assert no_profile_request_is_blocked(path, method), path


def test_targeted_limiter_success_only_clears_its_own_account():
    """one ip-wide limiter: signing in as yourself wiped your guesses at
    someone else's password, so the budget never ran out."""
    lim = TargetedLimiter(max_attempts=10, window_seconds=300, max_client_attempts=30)
    for i in range(9):
        lim.record_failure('ip', 'victim', 100.0 + i)
    lim.record_success('ip', 'me')
    lim.record_failure('ip', 'victim', 120.0)
    assert lim.is_locked('ip', 'victim', 121.0)[0] is True
    assert lim.is_locked('ip', 'me', 121.0)[0] is False


def test_targeted_limiter_caps_spraying_across_accounts():
    lim = TargetedLimiter(max_attempts=10, window_seconds=300, max_client_attempts=30)
    for i in range(30):
        lim.record_failure('ip', f'user{i}', 100.0)
    assert lim.is_locked('ip', 'someone-new', 101.0)[0] is True
    assert lim.is_locked('other-ip', 'someone-new', 101.0)[0] is False


def test_download_check_denies_a_missing_profile():
    """int(pid or 1) turned 'no profile' into the admin and allowed it."""
    rows = {5: {'id': 5, 'can_download': False}, 6: {'id': 6, 'can_download': True}}
    assert download_denied_reason(None, rows.get)
    assert download_denied_reason(99, rows.get) is None  # no row is not a no
    assert download_denied_reason(5, rows.get)
    assert download_denied_reason(6, rows.get) is None
    assert download_denied_reason(1, rows.get) is None
    assert profile_can_download({'id': 1, 'can_download': False}) is True
    assert profile_can_download({'id': 5, 'can_download': False}) is False
    assert profile_can_download(None) is False


def test_owner_profile_can_only_be_managed_by_itself():
    """a second admin could reset profile 1's pin/password and take over."""
    assert may_manage_profile(1, True, 1)
    assert not may_manage_profile(7, True, 1)
    assert may_manage_profile(7, True, 5)
    assert not may_manage_profile(5, False, 6)
    assert may_manage_profile(5, False, 5)
    assert not may_manage_profile(None, True, 5)


def test_profile_list_hides_private_fields_from_other_profiles():
    """GET /api/profiles handed recovery questions and library folders to the
    lock screen and to every member."""
    row = {'id': 3, 'name': 'kim', 'is_admin': False, 'has_pin': True,
           'recovery_question': 'first pet?', 'library_root': '/music/kim',
           'listenbrainz_username': 'kimlb'}
    member_view = profile_view_for(row, viewer_id=4, viewer_is_admin=False)
    assert member_view['name'] == 'kim' and member_view['has_pin'] is True
    assert 'recovery_question' not in member_view and 'library_root' not in member_view
    assert profile_view_for(row, viewer_id=3, viewer_is_admin=False) == row
    assert profile_view_for(row, viewer_id=None, viewer_is_admin=True) == row


def test_issue_lifecycle_rejects_bad_values_and_stamps_resolution():
    ok, err, code = clean_update({'status': 'closed'}, is_admin=True, is_owner=True, actor_id=1)
    assert ok is None and code == 400
    ok, err, code = clean_update({'priority': 'urgent!!'}, is_admin=True, is_owner=True, actor_id=1)
    assert ok is None and code == 400
    ok, err, code = clean_update({'status': 'resolved'}, is_admin=True, is_owner=True, actor_id=7)
    assert ok['resolved_by'] == 7 and ok['resolved_at']
    # reopening to in_progress clears the stamps (video kept stale ones)
    ok, err, code = clean_update({'status': 'in_progress'}, is_admin=True, is_owner=True, actor_id=7)
    assert ok['resolved_by'] is None and ok['resolved_at'] is None
    # an owner touching triage fields is refused, not partially applied
    ok, err, code = clean_update({'title': 'x', 'status': 'resolved'}, is_admin=False,
                                 is_owner=True, actor_id=5)
    assert ok is None and code == 403
    ok, err, code = clean_update({'description': 'y' * 5000}, is_admin=False, is_owner=True, actor_id=5)
    assert len(ok['description']) == 2000


def test_reporters_cannot_pick_their_own_priority():
    fields, _ = clean_new_issue({'title': 't', 'priority': 'high'}, is_admin=False)
    assert fields['priority'] == 'normal'
    fields, _ = clean_new_issue({'title': 't', 'priority': 'high'}, is_admin=True)
    assert fields['priority'] == 'high'
    assert clean_new_issue({'title': '   '}, is_admin=True)[1]


def test_issue_visibility_is_owner_or_admin():
    issue = {'profile_id': 5}
    assert visible_to(issue, is_admin=False, profile_id=5)
    assert not visible_to(issue, is_admin=False, profile_id=6)
    assert visible_to(issue, is_admin=True, profile_id=6)
    assert not visible_to(None, is_admin=True, profile_id=1)


# ── routes, for real ─────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


def _anon_client():
    """a browser that hasn't picked a profile (no admin session preset)."""
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


def _client_as(pid, **extra):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
        s.update(extra)
    return c


@pytest.fixture
def member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'member_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def second_member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'member2_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def admin_pin():
    """give profile 1 a pin for the test, put it back after."""
    from werkzeug.security import generate_password_hash
    db = web_server.get_database()
    with db._get_connection() as conn:
        before = conn.execute("SELECT pin_hash FROM profiles WHERE id = 1").fetchone()[0]
        conn.execute("UPDATE profiles SET pin_hash = ? WHERE id = 1",
                     (generate_password_hash('4321', method='pbkdf2:sha256'),))
        conn.commit()
    web_server._launch_pin_limiter.reset()
    yield '4321'
    web_server._launch_pin_limiter.reset()
    with db._get_connection() as conn:
        conn.execute("UPDATE profiles SET pin_hash = ? WHERE id = 1", (before,))
        conn.commit()


def _config(monkeypatch, **overrides):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: overrides[k] if k in overrides else real_get(k, d))


def test_member_logout_does_not_become_admin(member):
    """POST /api/profiles/logout popped the profile and the next request ran
    as profile 1 with admin rights."""
    c = _client_as(member)
    assert c.get('/api/debug-info').status_code == 403
    assert c.post('/api/profiles/logout').status_code == 200
    r = c.get('/api/debug-info')
    assert r.status_code == 401 and r.get_json()['profile_required'] is True
    # and it can't mint itself an api key either
    assert c.post('/api/v1/api-keys-internal/generate', json={'label': 'x'}).status_code == 401


def test_deleted_profile_session_does_not_become_admin(member):
    # `member` keeps it a multi-profile install once the other one is gone;
    # with only the admin left, no profile IS the admin (single-user posture)
    db = web_server.get_database()
    pid = db.create_profile(name=f'gone_{uuid4().hex[:8]}')
    c = _client_as(pid)
    db.delete_profile(pid)
    assert c.get('/api/debug-info').status_code == 401     # the stale id is popped
    assert c.get('/api/debug-info').status_code == 401     # and stays no-rights after


def test_login_mode_profile_logout_ends_the_login(member, monkeypatch):
    _config(monkeypatch, **{'security.require_login': True})
    c = _client_as(member, login_authenticated=True)
    assert c.get('/api/profiles/me/connections').status_code == 200
    c.post('/api/profiles/logout')
    assert c.get('/api/debug-info').status_code == 401
    with c.session_transaction() as s:
        assert 'login_authenticated' not in s


def test_single_profile_wrong_pin_does_not_open_the_launch_lock(admin_pin, monkeypatch):
    """select skipped the pin check with one profile, then set
    launch_pin_verified for ANY pin on profile 1."""
    _config(monkeypatch, **{'security.require_pin_on_launch': True})
    db = web_server.get_database()
    admin = db.get_profile(1)
    monkeypatch.setattr(type(db), 'get_all_profiles', lambda self: [admin])
    c = _anon_client()
    r = c.post('/api/profiles/select', json={'profile_id': 1, 'pin': 'wrong'})
    assert r.status_code == 401
    with c.session_transaction() as s:
        assert not s.get('launch_pin_verified')
    r = c.post('/api/profiles/select', json={'profile_id': 1, 'pin': admin_pin})
    assert r.status_code == 200
    with c.session_transaction() as s:
        assert s.get('launch_pin_verified') is True


def test_profile_pin_guessing_is_rate_limited(member):
    from werkzeug.security import generate_password_hash
    db = web_server.get_database()
    db.update_profile(member, pin_hash=generate_password_hash('1111', method='pbkdf2:sha256'))
    web_server._launch_pin_limiter.reset()
    try:
        c = _anon_client()
        for _ in range(10):
            assert c.post('/api/profiles/select',
                          json={'profile_id': member, 'pin': '0000'}).status_code == 401
        r = c.post('/api/profiles/select', json={'profile_id': member, 'pin': '1111'})
        assert r.status_code == 429
    finally:
        web_server._launch_pin_limiter.reset()


def test_credential_reset_only_clears_the_admin_pin(member, monkeypatch):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: 'known-secret-123' if k == 'acoustid.api_key' else real_get(k, d))
    monkeypatch.setattr(web_server.config_manager, 'set', lambda *a, **k: None)
    web_server._launch_pin_limiter.reset()
    try:
        c = _anon_client()
        r = c.post('/api/profiles/reset-pin-via-credential',
                   json={'credential': 'known-secret-123', 'profile_id': member})
        assert r.status_code == 403
        for _ in range(10):
            c.post('/api/profiles/reset-pin-via-credential', json={'credential': 'nope-nope'})
        r = c.post('/api/profiles/reset-pin-via-credential', json={'credential': 'known-secret-123'})
        assert r.status_code == 429
    finally:
        web_server._launch_pin_limiter.reset()


def test_clearing_the_admin_pin_turns_the_launch_lock_off(admin_pin, monkeypatch):
    """a lock left on with no pin accepted any pin."""
    calls = []
    monkeypatch.setattr(web_server.config_manager, 'set', lambda k, v: calls.append((k, v)))
    c = _client_as(1)
    assert c.post('/api/profiles/1/set-pin', json={'pin': ''}).get_json()['success']
    assert ('security.require_pin_on_launch', False) in calls


def test_second_admin_cannot_take_over_profile_one(member):
    db = web_server.get_database()
    db.update_profile(member, is_admin=1)
    c = _client_as(member)
    assert c.post('/api/profiles/1/set-pin', json={'pin': '9999'}).status_code == 403
    assert c.post('/api/profiles/1/set-password', json={'password': 'takeover'}).status_code == 403
    assert c.put('/api/profiles/1', json={'is_admin': False}).status_code == 403


def test_case_variant_profile_names_are_refused(member):
    name = web_server.get_database().get_profile(member)['name']
    c = _client_as(1)
    r = c.post('/api/profiles', json={'name': name.upper()})
    assert r.status_code == 409


def test_member_profile_list_hides_other_profiles_private_fields(member, second_member):
    db = web_server.get_database()
    db.set_profile_recovery(second_member, 'first pet?', 'rex')
    profiles = _client_as(member).get('/api/profiles').get_json()['profiles']
    other = next(p for p in profiles if p['id'] == second_member)
    assert 'recovery_question' not in other
    admin_view = _client_as(1).get('/api/profiles').get_json()['profiles']
    assert next(p for p in admin_view if p['id'] == second_member)['recovery_question'] == 'first pet?'


def test_issues_ignore_a_spoofed_profile_header(member, second_member):
    """music /api/issues read X-Profile-Id for identity: no header or '1'
    made any profile the admin."""
    db = web_server.get_database()
    theirs = db.create_issue(profile_id=second_member, entity_type='album', entity_id='1',
                             category='other', title='theirs')['id']
    try:
        c = _client_as(member)
        spoof = {'X-Profile-Id': '1'}
        listed = c.get('/api/issues', headers=spoof).get_json()['issues']
        assert all(i['profile_id'] == member for i in listed)
        assert c.get(f'/api/issues/{theirs}', headers=spoof).status_code == 404
        assert c.put(f'/api/issues/{theirs}', json={'status': 'resolved'},
                     headers=spoof).status_code == 404
        assert c.delete(f'/api/issues/{theirs}', headers=spoof).status_code == 404
        mine = c.post('/api/issues', headers={'X-Profile-Id': str(second_member)},
                      json={'entity_type': 'album', 'entity_id': '2', 'category': 'other',
                            'title': 'mine', 'priority': 'high'}).get_json()['id']
        row = db.get_issue(mine)
        assert row['profile_id'] == member and row['priority'] == 'normal'
        assert c.put(f'/api/issues/{mine}', json={'status': 'resolved'}).status_code == 403
        admin = _client_as(1)
        assert admin.put(f'/api/issues/{mine}', json={'status': 'closed'}).status_code == 400
        assert admin.put(f'/api/issues/{mine}', json={'status': 'resolved'}).get_json()['success']
        assert db.get_issue(mine)['resolved_by'] == 1
        db.delete_issue(mine)
    finally:
        db.delete_issue(theirs)


def test_audiobook_scope_ignores_a_members_header(member, second_member):
    from api.helpers import acting_profile_id
    with web_server.app.test_request_context('/api/audiobooks/wishlist',
                                             headers={'X-Profile-Id': str(second_member)}):
        from flask import g
        g.profile_id = member
        g.is_admin = False
        assert acting_profile_id(__import__('flask').request) == member
        g.profile_id = 1
        g.is_admin = True
        assert acting_profile_id(__import__('flask').request) == second_member


def test_no_download_profile_is_refused_by_the_ungated_routes(member):
    web_server.get_database().update_profile(member, can_download=0)
    c = _client_as(member)
    assert c.post('/api/wishlist/process').status_code == 403
    assert c.post('/api/music-video/download', json={'video_id': 'x', 'url': 'y'}).status_code == 403
    assert c.post('/api/library/track/1/redownload/start', json={}).status_code == 403


def test_member_cannot_read_logs(member):
    c = _client_as(member)
    assert c.get('/api/logs/tail').status_code == 403
    assert c.get('/api/debug-info').status_code == 403


def test_scheduled_wishlist_run_only_takes_approved_rows_without_download_rights(monkeypatch):
    """the auto-wishlist run downloaded every profile's list, so a profile
    with downloads off just added to its wishlist and waited. now their rows
    are requests: only the ones an admin approved get downloaded."""
    import contextlib
    import logging
    from types import SimpleNamespace

    from core.wishlist import processing

    asked = []

    class _Service:
        def get_wishlist_count(self, profile_id, approved_only=False):
            asked.append((profile_id, approved_only))
            return 0

    monkeypatch.setattr(processing, 'get_wishlist_service', lambda: _Service())
    profiles = [{'id': 1, 'can_download': True},
                {'id': 5, 'can_download': False},
                {'id': 6, 'can_download': True}]
    runtime = SimpleNamespace(
        logger=logging.getLogger('test'),
        is_actually_processing=lambda: False,
        processing_guard=lambda: contextlib.nullcontext(True),
        app_context_factory=contextlib.nullcontext,
        get_profiles_database=lambda: SimpleNamespace(get_all_profiles=lambda: profiles),
        update_automation_progress=lambda *a, **k: None,
    )
    processing.process_wishlist_automatically(runtime)
    assert sorted(asked) == [(1, False), (5, True), (6, False)]
