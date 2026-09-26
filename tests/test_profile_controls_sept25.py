"""profile controls built sept 25 2026: sign out everywhere, invite links, the
admin audit log, avatar upload, request quotas, approve-all and the
server-side page gate. each test says the gap it closes.
"""

from __future__ import annotations

import io
import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-controls-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'controls.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'
os.environ['SOULSYNC_AVATAR_DIR'] = os.path.join(_TMP, 'avatars')

from core.permissions import page_denied, page_for_path  # noqa: E402
from core.requests.quota import over_quota, quota_for, quota_state  # noqa: E402
from core.security.session_profile import is_open_profile_path  # noqa: E402


# ── pure rules ───────────────────────────────────────────────────────────────

def test_quota_only_limits_limited_members():
    assert quota_for({'id': 1, 'request_limit': 3}) is None
    assert quota_for({'id': 4, 'is_admin': True, 'request_limit': 3}) is None
    assert quota_for({'id': 5, 'request_limit': 0}) is None
    q = quota_for({'id': 5, 'request_limit': 3, 'request_limit_days': 7})
    assert q == {'limit': 3, 'days': 7}
    assert not over_quota(q, 2) and over_quota(q, 3)
    assert quota_state(q, 1)['remaining'] == 2


def test_page_gate_covers_only_page_exclusive_apis():
    assert page_for_path('/api/import/inbox') == 'import'
    assert page_for_path('/api/audiobooks/wishlist') == 'audiobooks'
    assert page_for_path('/api/stats/overview') is None          # the dashboard reads it too
    assert page_denied('/api/audiobooks/x', ['dashboard'], False)
    assert not page_denied('/api/audiobooks/x', None, False)       # no list = every page
    assert not page_denied('/api/audiobooks/x', ['dashboard'], True)
    assert not page_denied('/api/audiobooks/x', ['audiobooks'], False)


def test_invites_and_avatars_are_open_before_sign_in():
    assert is_open_profile_path('/api/invite/abc', 'GET')
    assert is_open_profile_path('/api/invite/abc/accept', 'POST')
    assert is_open_profile_path('/api/profiles/3/avatar', 'GET')
    assert not is_open_profile_path('/api/profiles/3/avatar', 'POST')
    assert not is_open_profile_path('/api/profiles/3', 'GET')


# ── routes, for real ─────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


def _as(pid, **extra):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
        s.update(extra)
    return c


def _anon():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


@pytest.fixture
def member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'm_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


@pytest.fixture(autouse=True)
def _fresh_epoch_cache():
    from core.security.session_epoch import _reset_for_tests
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_sign_out_everywhere_ends_other_sessions_but_keeps_this_one(member):
    """signed cookies can't be deleted server-side; a stolen or forgotten
    browser stayed signed in forever."""
    here = _as(member)
    elsewhere = _as(member)
    assert elsewhere.get('/api/profiles/me/connections').status_code == 200
    assert here.post(f'/api/profiles/{member}/sign-out-everywhere').get_json()['success']
    r = elsewhere.get('/api/profiles/me/connections')
    assert r.status_code == 401 and r.get_json()['signed_out'] is True
    assert here.get('/api/profiles/me/connections').status_code == 200


def test_a_member_cannot_sign_someone_else_out(member):
    db = web_server.get_database()
    other = db.create_profile(name=f'o_{uuid4().hex[:8]}')
    try:
        assert _as(member).post(f'/api/profiles/{other}/sign-out-everywhere').status_code == 403
        assert _as(1).post(f'/api/profiles/{other}/sign-out-everywhere').get_json()['success']
    finally:
        db.delete_profile(other)


def test_a_new_password_signs_out_other_browsers(member):
    here = _as(member)
    elsewhere = _as(member)
    assert here.post(f'/api/profiles/{member}/set-password', json={'password': 'newpass1'}).get_json()['success']
    assert elsewhere.get('/api/profiles/me/connections').status_code == 401
    assert here.get('/api/profiles/me/connections').status_code == 200


def test_invite_link_creates_a_profile_once_with_its_access():
    admin = _as(1)
    r = admin.post('/api/profiles/invites', json={
        'preset': {'allowed_sides': 'music', 'can_download': False, 'max_rating': 'PG', 'is_admin': True},
        'note': 'for grandma', 'expires_hours': 24})
    assert r.status_code == 201
    token = r.get_json()['token']
    guest = _anon()
    info = guest.get(f'/api/invite/{token}').get_json()
    assert info['note'] == 'for grandma' and info['can_download'] is False
    name = f'gran_{uuid4().hex[:6]}'
    r = guest.post(f'/api/invite/{token}/accept', json={'name': name, 'pin': '2468'})
    assert r.status_code == 201
    pid = r.get_json()['profile_id']
    db = web_server.get_database()
    try:
        row = db.get_profile(pid)
        assert row['can_download'] is False and row['allowed_sides'] == 'music'
        assert row['max_rating'] == 'PG' and row['is_admin'] is False   # a preset can't make an admin
        assert guest.get('/api/profiles/me/connections').status_code == 200   # signed in as them
        # spent: a second use fails
        assert _anon().post(f'/api/invite/{token}/accept', json={'name': 'x' + name}).status_code == 404
        entries = admin.get('/api/profiles/audit').get_json()['entries']
        assert any(e['action'] == 'invite_used' and e['target_id'] == pid for e in entries)
    finally:
        db.delete_profile(pid)


def test_invite_management_is_admin_only(member):
    assert _as(member).post('/api/profiles/invites', json={}).status_code == 403
    assert _as(member).get('/api/profiles/invites').status_code == 403
    assert _anon().get('/api/invite/not-a-real-token').status_code == 404


def test_admin_actions_land_in_the_audit_log(member):
    admin = _as(1)
    admin.put(f'/api/profiles/{member}', json={'can_download': False})
    admin.post(f'/api/profiles/{member}/set-pin', json={'pin': '1357'})
    entries = admin.get('/api/profiles/audit').get_json()['entries']
    mine = [e['action'] for e in entries if e['target_id'] == member]
    assert 'profile_updated' in mine and 'pin_reset' in mine
    assert _as(member).get('/api/profiles/audit').status_code == 403


def test_avatar_upload_is_reencoded_and_served_before_sign_in(member):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (900, 600), (200, 30, 90)).save(buf, 'PNG')
    buf.seek(0)
    r = _as(member).post(f'/api/profiles/{member}/avatar',
                         data={'file': (buf, 'me.png')}, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    url = r.get_json()['avatar_url']
    got = _anon().get(url.split('?')[0])
    assert got.status_code == 200 and got.mimetype == 'image/webp'
    img = Image.open(io.BytesIO(got.data))
    assert img.size == (512, 512)
    bad = _as(member).post(f'/api/profiles/{member}/avatar',
                           data={'file': (io.BytesIO(b'<svg onload=alert(1)>'), 'x.svg')},
                           content_type='multipart/form-data')
    assert bad.status_code == 400


def test_video_request_quota(member, tmp_path, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    from database.video_database import VideoDatabase
    monkeypatch.setattr(videoapi, '_video_db', VideoDatabase(database_path=str(tmp_path / 'v.db')))
    monkeypatch.setattr(vreq, '_tmdb_lookup', lambda kind, tid: None)
    db = web_server.get_database()
    db.update_profile(member, request_limit=2, request_limit_days=7, allowed_sides='both')
    c = _as(member)
    for tid in (11, 12):
        assert c.post('/api/video/requests', json={'kind': 'movie', 'tmdb_id': tid, 'title': 'x'}).status_code == 200
    r = c.post('/api/video/requests', json={'kind': 'movie', 'tmdb_id': 13, 'title': 'y'})
    assert r.status_code == 429 and r.get_json()['quota']['remaining'] == 0
    # asking again for one already waiting isn't a new ask
    assert c.post('/api/video/requests', json={'kind': 'movie', 'tmdb_id': 11, 'title': 'x'}).status_code == 200
    assert c.get('/api/video/requests').get_json()['quota']['used'] == 2


def _track(tid, album_id, typ='album'):
    return {'id': tid, 'name': f'song {tid}', 'artists': [{'name': 'A'}],
            'album': {'id': album_id, 'name': f'alb {album_id}', 'album_type': typ, 'images': []}}


def test_music_request_quota_counts_asks_not_tracks(member):
    db = web_server.get_database()
    db.update_profile(member, can_download=0, request_limit=2, request_limit_days=7)
    tag = uuid4().hex[:6]
    add = lambda t, src='album': db.add_to_wishlist_detailed(  # noqa: E731
        spotify_track_data=t, source_type=src, profile_id=member, user_initiated=True)
    # one album of three tracks is one ask
    for i in range(3):
        assert add(_track(f'{tag}a{i}', f'A{tag}'))['status'] != 'rejected'
    assert add(_track(f'{tag}s1', f'S{tag}', 'single'), 'manual')['status'] != 'rejected'
    out = add(_track(f'{tag}s2', f'T{tag}', 'single'), 'manual')
    assert out['status'] == 'rejected' and out.get('reason') == 'request_limit'
    # more of an album already asked for still goes on
    assert add(_track(f'{tag}a9', f'A{tag}'))['status'] != 'rejected'
    q = _as(member).get('/api/requests/music/quota').get_json()['quota']
    assert q['used'] == 2 and q['remaining'] == 0
    db.clear_wishlist(profile_id=member)


def test_approve_all_music_requests(member):
    db = web_server.get_database()
    db.update_profile(member, can_download=0)
    tag = uuid4().hex[:6]
    for i in range(2):
        db.add_to_wishlist(spotify_track_data=_track(f'{tag}x{i}', f'X{tag}'), source_type='album',
                           profile_id=member, user_initiated=True)
    db.add_to_wishlist(spotify_track_data=_track(f'{tag}y', f'Y{tag}', 'single'), source_type='manual',
                       profile_id=member, user_initiated=True)
    assert _as(member).post('/api/requests/music/approve-all', json={}).status_code == 403
    r = _as(1).post('/api/requests/music/approve-all', json={'profile_id': member}).get_json()
    assert r['approved'] == 2
    assert len(db.get_wishlist_tracks(profile_id=member, approved_only=True)) == 3
    db.clear_wishlist(profile_id=member)


def test_a_page_left_off_the_list_refuses_its_api(member):
    """allowed_pages only hid the nav button: /api/audiobooks answered anyway."""
    db = web_server.get_database()
    db.update_profile(member, allowed_pages=['dashboard', 'library'])
    r = _as(member).get('/api/audiobooks/wishlist')
    assert r.status_code == 403 and r.get_json()['error'] == 'page_not_allowed'
    db.update_profile(member, allowed_pages=None)
    assert _as(member).get('/api/audiobooks/wishlist').status_code != 403
