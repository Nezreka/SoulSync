"""what the server is doing is the admins' (#2170, #2174).

a member's notification panel showed the server's numbers (downloads across
the server, uptime, memory), its health and its background jobs, and Help &
Docs offered them the diagnostics. hiding it in the page is not enough: the
feeds behind it refuse a member too. driven through web_server.app with a
member's session, the same way test_permissions_enforcement does.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-memops-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'memops.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402
from core.automation import progress as AP  # noqa: E402
from core.permissions import declared_access  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


@pytest.fixture
def member():
    db = _db()
    pid = db.create_profile(name='m_%s' % uuid4().hex[:8])
    tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'adult')
    PS.change_profile(db, db.get_profile(pid), {'template_id': tid, 'set': {}})
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


# ── #2170: the notification panel's feeds ────────────────────────────────

ADMIN_ONLY_FEEDS = (
    '/api/system/stats',        # downloads, speed, uptime, memory
    '/api/repair/progress',     # library repair jobs
)


@pytest.mark.parametrize('rule', ADMIN_ONLY_FEEDS)
def test_the_panel_feeds_declare_admin(rule):
    assert declared_access(_view(rule)) == ('admin', None)


@pytest.mark.parametrize('rule', ADMIN_ONLY_FEEDS + ('/api/video/health', '/api/video/bulk/status'))
def test_a_member_is_refused_the_server_feeds(member, rule):
    assert _as(member).get(rule).status_code == 403


def test_the_video_health_and_bulk_feeds_are_admin_in_the_route_table():
    from api.video.access import route_access
    assert route_access('/api/video/health', 'GET') == ('admin', None)
    assert route_access('/api/video/bulk/status', 'GET') == ('admin', None)


def test_the_admin_still_gets_the_numbers():
    r = _as(1).get('/api/system/stats')
    assert r.status_code == 200
    assert 'uptime' in (r.get_json() or {})


@pytest.fixture
def runs():
    AP.progress_states.clear()
    AP.progress_owners.clear()
    yield
    AP.progress_states.clear()
    AP.progress_owners.clear()


def test_a_member_hears_only_their_own_automation_runs(member, runs):
    AP.init_progress(901, 'Clean the recycle bin', 'clean_recycle_bin', owner_profile_id=None)
    AP.init_progress(902, 'My playlist pull', 'refresh_mirrored', owner_profile_id=member)
    AP.init_progress(903, 'Someone else', 'refresh_mirrored', owner_profile_id=member + 1000)
    mine = _as(member).get('/api/automations/progress').get_json()
    assert set(mine) == {'902'}
    every = _as(1).get('/api/automations/progress').get_json()
    assert {'901', '902', '903'} <= set(every)


def test_progress_pushes_go_to_the_admins_and_each_owner(runs):
    AP.init_progress(1, 'system', 'process_wishlist')
    AP.init_progress(2, 'sam', 'sync_playlist', owner_profile_id=7)
    sent = []
    payload = {'1': {'status': 'running'}, '2': {'status': 'running'}}
    AP.emit_scoped(lambda e, d, to=None: sent.append((e, sorted(d), to)), 'automation:progress',
                   payload, 'role:admin')
    assert ('automation:progress', ['1', '2'], 'role:admin') in sent
    assert ('automation:progress', ['2'], 'member:7') in sent
    # the system run reaches no member room at all
    assert all(to == 'role:admin' or '1' not in keys for _, keys, to in sent)


def test_visible_to_fails_closed():
    AP.progress_owners.clear()
    AP.progress_owners[5] = 3
    payload = {'5': {}, '6': {}}
    assert AP.visible_to(payload, None, False) == {}
    assert AP.visible_to(payload, 3, False) == {'5': {}}
    assert AP.visible_to(payload, 3, True) == payload
    AP.progress_owners.clear()


def test_the_app_wires_the_scoped_pushes():
    """the loops have no unit seam: hold the source to the room it sends to"""
    import inspect
    src = inspect.getsource(web_server)
    assert "socketio.emit('dashboard:stats', _build_system_stats(), to=ADMIN_SOCKET_ROOM)" in src
    assert "admin_room=ADMIN_SOCKET_ROOM" in src
    assert "socketio_emit=_emit_automation_progress" in src
    assert web_server.ADMIN_SOCKET_ROOM == 'role:admin'


# ── #2174: diagnostics behind Help & Docs ────────────────────────────────

DIAGNOSTICS = (
    '/api/debug-info',
    '/api/logs/tail',
    '/api/settings/log-level',
    '/api/help/health',
    '/api/help/bundle',
    '/api/help/logs',
    '/api/help/logs/download',
    '/api/help/logs/download-all',
)


@pytest.mark.parametrize('rule', DIAGNOSTICS)
def test_every_diagnostic_is_the_admins(rule):
    assert declared_access(_view(rule)) == ('admin', None)


@pytest.mark.parametrize('rule', ('/api/debug-info', '/api/logs/tail', '/api/help/logs',
                                  '/api/help/bundle'))
def test_a_member_is_refused_the_diagnostics(member, rule):
    assert _as(member).get(rule).status_code == 403
