"""the enrichment workers are the admins' (#2196).

a member could see the worker orbs on both dashboards, open Manage Workers,
read every worker's status and what it matched, and reach the controls.
hiding the orbs is not enough: every worker route refuses a member, music and
video, and the status pushes go to the admins' sockets only. driven through
web_server.app with a member's session, the way test_member_server_ops does.
"""

from __future__ import annotations

import inspect
import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-workers-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'workers.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402
from core.permissions import declared_access  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


@pytest.fixture
def member():
    """a fresh member each test (the shared database is reset by making a new
    row, never by reusing one an earlier test changed)"""
    db = _db()
    pid = db.create_profile(name='w_%s' % uuid4().hex[:8])
    tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'adult')
    # both sides on, so a refusal below is the admin rule, not a side that's off
    PS.change_profile(db, db.get_profile(pid), {'template_id': tid,
                                                'set': {'movies.access': True, 'tv.access': True}})
    assert _as(pid).get('/api/video/dashboard').status_code == 200
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


def _view(rule, method='GET'):
    for r in APP.url_map.iter_rules():
        if r.rule == rule and method in (r.methods or ()):
            return APP.view_functions[r.endpoint]
    raise AssertionError('no route %s %s' % (method, rule))


# ── music: every worker route declares admin ─────────────────────────────

MUSIC_ROUTES = (
    ('GET', '/api/enrichment/status-all'),
    ('GET', '/api/enrichment/<service_id>/status'),
    ('POST', '/api/enrichment/<service_id>/pause'),
    ('POST', '/api/enrichment/<service_id>/resume'),
    ('GET', '/api/enrichment/<service_id>/breakdown'),
    ('GET', '/api/enrichment/<service_id>/unmatched'),
    ('POST', '/api/enrichment/<service_id>/retry'),
    ('POST', '/api/enrichment/retry-all-failed'),
    ('POST', '/api/enrichment/<service_id>/verify-matches'),
    ('POST', '/api/enrichment/verify-matches'),
    ('GET', '/api/enrichment/<service_id>/priority'),
    ('POST', '/api/enrichment/<service_id>/priority'),
    ('GET', '/api/soulid/status'),
    ('GET', '/api/hydrabase-worker/status'),
    ('POST', '/api/hydrabase-worker/pause'),
    ('POST', '/api/hydrabase-worker/resume'),
)


@pytest.mark.parametrize('method,rule', MUSIC_ROUTES)
def test_every_music_worker_route_is_admin(method, rule):
    assert declared_access(_view(rule, method)) == ('admin', None)


def test_no_enrichment_route_is_left_out():
    """a worker route added later is held to the same rule"""
    listed = {(m, r) for m, r in MUSIC_ROUTES}
    for r in APP.url_map.iter_rules():
        if not r.rule.startswith('/api/enrichment/'):
            continue
        for m in (r.methods or ()) - {'HEAD', 'OPTIONS'}:
            assert (m, r.rule) in listed, 'unlisted worker route %s %s' % (m, r.rule)


MEMBER_READS = (
    '/api/enrichment/status-all',
    '/api/enrichment/spotify/status',
    '/api/enrichment/musicbrainz/status',
    '/api/enrichment/spotify/breakdown',
    '/api/enrichment/spotify/unmatched',
    '/api/enrichment/spotify/priority',
    '/api/soulid/status',
    '/api/hydrabase-worker/status',
)
MEMBER_WRITES = (
    '/api/enrichment/spotify/pause',
    '/api/enrichment/spotify/resume',
    '/api/enrichment/spotify/retry',
    '/api/enrichment/retry-all-failed',
    '/api/enrichment/spotify/verify-matches',
    '/api/enrichment/verify-matches',
    '/api/enrichment/spotify/priority',
    '/api/hydrabase-worker/pause',
    '/api/hydrabase-worker/resume',
)


@pytest.mark.parametrize('url', MEMBER_READS)
def test_a_member_is_refused_the_worker_reads(member, url):
    assert _as(member).get(url).status_code == 403


@pytest.mark.parametrize('url', MEMBER_WRITES)
def test_a_member_is_refused_the_worker_controls(member, url):
    assert _as(member).post(url, json={}).status_code == 403


def test_the_admin_still_reads_the_workers():
    c = _as(1)
    r = c.get('/api/enrichment/status-all')
    assert r.status_code == 200
    assert 'services' in (r.get_json() or {})
    assert c.get('/api/soulid/status').status_code == 200
    assert c.get('/api/hydrabase-worker/status').status_code == 200


# ── video: the whole prefix is the admins' ───────────────────────────────

VIDEO_ROUTES = (
    ('GET', '/api/video/enrichment/services'),
    ('GET', '/api/video/enrichment/coverage'),
    ('GET', '/api/video/enrichment/status-all'),
    ('GET', '/api/video/enrichment/tmdb/status'),
    ('POST', '/api/video/enrichment/tmdb/pause'),
    ('POST', '/api/video/enrichment/tmdb/resume'),
    ('GET', '/api/video/enrichment/tmdb/breakdown'),
    ('GET', '/api/video/enrichment/tmdb/unmatched'),
    ('POST', '/api/video/enrichment/tmdb/retry'),
    ('POST', '/api/video/enrichment/retry-all-failed'),
    ('GET', '/api/video/enrichment/priority'),
    ('POST', '/api/video/enrichment/priority'),
    ('GET', '/api/video/enrichment/config'),
    ('GET', '/api/video/enrichment/matches/movie/1'),
)


@pytest.mark.parametrize('method,path', VIDEO_ROUTES)
def test_every_video_worker_path_is_admin_in_the_route_table(method, path):
    from api.video.access import route_access
    assert route_access(path, method) == ('admin', None)


@pytest.mark.parametrize('method,path', VIDEO_ROUTES)
def test_a_member_is_refused_the_video_workers(member, method, path):
    c = _as(member)
    r = c.get(path) if method == 'GET' else c.post(path, json={})
    assert r.status_code == 403


def test_the_admin_still_reads_the_video_workers():
    r = _as(1).get('/api/video/enrichment/status-all')
    assert r.status_code == 200


# ── the pushes ───────────────────────────────────────────────────────────

def test_worker_pushes_go_to_the_admins_room():
    room = web_server.ADMIN_SOCKET_ROOM
    for name in ('musicbrainz', 'spotify-enrichment', 'hydrabase', 'soulid', 'listening-stats'):
        assert web_server._enrichment_status_room(name) == room
    # the Tools page (grantable to a member) reads repair's frame
    assert web_server._enrichment_status_room('repair') is None


def test_the_loops_send_to_the_rooms():
    """the loops have no unit seam: hold the source to the room it sends to"""
    music = inspect.getsource(web_server._emit_enrichment_status_loop)
    assert "socketio.emit(f'enrichment:{name}', status, to=_enrichment_status_room(name))" in music
    video = inspect.getsource(web_server._emit_video_enrichment_status_loop)
    assert "socketio.emit(f'enrichment:{svc}', stats_cache.get(svc, w), to=ADMIN_SOCKET_ROOM)" in video
    assert "socketio.emit('enrichment:youtube', get_youtube_date_enricher().stats(), to=ADMIN_SOCKET_ROOM)" in video
    # no other emit of a worker frame in either loop
    assert music.count('socketio.emit(') == 1
    assert video.count('socketio.emit(') == 2
