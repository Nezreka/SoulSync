"""the server's library state and its operational notices are the admins'
(#2207, #2208).

#2207: a member's dashboard showed the media server's status, the database's
size, the disk the video library takes, and offered review and the poster
manager. the size and the scan times reached them on the dashboard:db_stats
push, which went to every socket; /api/video/dashboard handed them the disk
size.

#2208: the spotify ban (its modal, its countdown, the endpoint that tripped
it) rode the status:update push to every socket, and the rate monitor
(api call rates, worker states, the ban) went to everyone too.

driven through web_server.app with a member's session, the way
test_member_server_ops does.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-memdash-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'memdash.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


@pytest.fixture
def member():
    db = _db()
    pid = db.create_profile(name='m_%s' % uuid4().hex[:8])
    tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'adult')
    PS.change_profile(db, db.get_profile(pid), {'template_id': tid,
                                                'set': {'movies.access': True, 'tv.access': True}})
    yield pid
    try:
        db.delete_profile(pid)
    except Exception:  # noqa: BLE001
        pass


def _as(pid):
    c = APP.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


BANNED = {
    'authenticated': True,
    'rate_limited': True,
    'rate_limit': {'active': True, 'remaining_seconds': 13800, 'retry_after': 14400,
                   'endpoint': 'search_artists'},
    'post_ban_cooldown': 0,
}


# ── #2207: the library card's numbers ────────────────────────────────────

def test_a_member_gets_the_counts_and_nothing_else(member):
    r = _as(member).get('/api/database/stats')
    assert r.status_code == 200
    assert set(r.get_json()) == {'artists', 'albums', 'tracks'}


def test_the_admin_still_gets_the_size_and_the_scan_times():
    r = _as(1).get('/api/database/stats')
    assert r.status_code == 200
    body = r.get_json()
    assert 'database_size_mb' in body
    assert 'last_update' in body


def test_the_db_stats_push_goes_to_the_admins_only(monkeypatch):
    sent = []
    monkeypatch.setattr(web_server.socketio, 'emit',
                        lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(web_server, '_has_connected_clients', lambda: True)
    db = MagicMock()
    db.get_database_info_for_server.return_value = {'tracks': 1, 'database_size_mb': 9.0}
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    _one_tick(monkeypatch, web_server._emit_db_stats_loop)
    assert sent == [(('dashboard:db_stats', {'tracks': 1, 'database_size_mb': 9.0}),
                     {'to': web_server.ADMIN_SOCKET_ROOM})]


def test_a_member_gets_no_disk_size_on_the_video_dashboard(member):
    r = _as(member).get('/api/video/dashboard')
    assert r.status_code == 200
    lib = r.get_json()['library']
    assert 'size_bytes' not in lib
    assert 'movies' in lib


def test_the_admin_keeps_the_video_disk_size():
    r = _as(1).get('/api/video/dashboard')
    assert r.status_code == 200
    assert 'size_bytes' in r.get_json()['library']


# ── #2208: the spotify ban and the other operational pushes ──────────────

class _Stop(Exception):
    pass


def _one_tick(monkeypatch, loop):
    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        if calls['n'] > 1:
            raise _Stop()
    monkeypatch.setattr(web_server.socketio, 'sleep', fake_sleep)
    with pytest.raises(_Stop):
        loop()


def test_the_member_payload_has_no_ban_and_no_workers():
    full = {'spotify': dict(BANNED), 'enrichment': {'spotify_enrichment': {'running': True}},
            'media_server': {'connected': True}, 'active_downloads': 2}
    out = web_server._status_payload_for_member(full)
    assert out['spotify']['rate_limited'] is False
    assert out['spotify']['rate_limit'] is None
    assert out['spotify']['post_ban_cooldown'] == 0
    assert out['spotify']['authenticated'] is True
    assert out['enrichment'] == {}
    # what a member's pages read is untouched
    assert out['media_server'] == {'connected': True}
    assert out['active_downloads'] == 2
    # and the admin's copy is not changed under them
    assert full['spotify']['rate_limited'] is True
    assert full['enrichment']


def test_the_status_push_is_whole_for_admins_and_trimmed_for_members(monkeypatch):
    sent = []
    monkeypatch.setattr(web_server.socketio, 'emit',
                        lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(web_server, '_has_connected_clients', lambda: True)
    monkeypatch.setattr(web_server, '_build_status_payload',
                        lambda: {'spotify': dict(BANNED), 'enrichment': {'x': 1}})
    _one_tick(monkeypatch, web_server._emit_service_status_loop)
    by_room = {k.get('to'): a for a, k in sent}
    assert set(by_room) == {web_server.ADMIN_SOCKET_ROOM, web_server.MEMBER_SOCKET_ROOM}
    assert all(a[0] == 'status:update' for a in by_room.values())
    assert by_room[web_server.ADMIN_SOCKET_ROOM][1]['spotify']['rate_limit']['endpoint'] == 'search_artists'
    member_sp = by_room[web_server.MEMBER_SOCKET_ROOM][1]['spotify']
    assert member_sp['rate_limited'] is False and member_sp['rate_limit'] is None
    # nothing goes to every socket
    assert None not in by_room


def test_the_status_route_trims_the_ban_for_a_member(member, monkeypatch):
    monkeypatch.setattr(web_server, '_spotify_status_with_availability',
                        lambda _sp: dict(BANNED))
    body = _as(member).get('/status').get_json()
    assert body['spotify']['rate_limited'] is False
    assert body['spotify']['rate_limit'] is None
    assert body['enrichment'] == {}


def test_the_status_route_keeps_the_ban_for_the_admin(monkeypatch):
    monkeypatch.setattr(web_server, '_spotify_status_with_availability',
                        lambda _sp: dict(BANNED))
    body = _as(1).get('/status').get_json()
    assert body['spotify']['rate_limited'] is True
    assert body['spotify']['rate_limit']['endpoint'] == 'search_artists'


@pytest.mark.parametrize('rule', ('/api/spotify/rate-limit-status',
                                  '/api/rate-monitor/history/spotify'))
def test_a_member_is_refused_the_ban_and_the_rates(member, rule):
    assert _as(member).get(rule).status_code == 403


def test_the_admin_still_reads_the_ban():
    assert _as(1).get('/api/spotify/rate-limit-status').status_code == 200


def test_the_rate_monitor_push_goes_to_the_admins_only(monkeypatch):
    sent = []
    monkeypatch.setattr(web_server.socketio, 'emit',
                        lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(web_server, '_has_connected_clients', lambda: True)
    tracker = MagicMock()
    tracker.get_all_rates.return_value = {}
    monkeypatch.setitem(__import__('sys').modules, 'core.api_call_tracker',
                        MagicMock(api_call_tracker=tracker))
    _one_tick(monkeypatch, web_server._emit_rate_monitor_loop)
    assert [(a[0], k.get('to')) for a, k in sent] == [
        ('rate-monitor:update', web_server.ADMIN_SOCKET_ROOM)]


# ── the rooms ────────────────────────────────────────────────────────────

def _rooms_for(monkeypatch, pid, is_admin):
    joined, left = [], []
    monkeypatch.setattr(web_server, '_ws_session_profile', lambda: (pid, is_admin))
    monkeypatch.setattr(web_server, 'join_room', joined.append)
    monkeypatch.setattr(web_server, 'leave_room', left.append)
    monkeypatch.setattr(web_server, '_ws_profile_may_chat', lambda _pid: False)
    web_server._ws_join_role_room()
    return joined, left


def test_a_member_socket_joins_the_member_room(monkeypatch):
    joined, left = _rooms_for(monkeypatch, 7, False)
    assert web_server.MEMBER_SOCKET_ROOM in joined
    assert web_server.ADMIN_SOCKET_ROOM not in joined
    assert 'member:7' in joined


def test_an_admin_socket_is_never_in_the_member_room(monkeypatch):
    joined, left = _rooms_for(monkeypatch, 1, True)
    assert web_server.ADMIN_SOCKET_ROOM in joined
    assert web_server.MEMBER_SOCKET_ROOM not in joined
    # a socket that was a member's before (a profile switch) leaves it
    assert web_server.MEMBER_SOCKET_ROOM in left


def test_no_profile_is_no_room(monkeypatch):
    joined, _left = _rooms_for(monkeypatch, None, False)
    assert joined == []
