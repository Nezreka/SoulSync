"""piece A of people and permissions (#2166): the server holds every music,
shell and profile route to what the caller's permissions say.

the routes are driven for real through web_server.app with sessions set the
way a browser has them: members on a template with personal changes (the
same layers the people editor writes), a moderator, an admin. a refused
route answers the calm refusal (core.permissions.refusal_body) before its
view runs, so the generated checks below call every declared route without
side effects.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from uuid import uuid4

import pytest
from flask import Flask, g

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-perma-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'perma.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402
from core import permissions as P  # noqa: E402
from tests.permissions_fixtures import member_ctx, member_perms  # noqa: E402
from tests.test_route_permissions import STILL_LISTED_PREFIXES, inventory  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


def _member(template='adult', **changes):
    """a non-admin profile following a built-in template with these personal
    changes, written the way PUT /api/profiles/<id>/permissions writes them."""
    db = _db()
    pid = db.create_profile(name='m_%s' % uuid4().hex[:8])
    tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == template)
    PS.change_profile(db, db.get_profile(pid), {'template_id': tid, 'set': changes})
    return pid


def _as(pid):
    c = APP.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def made():
    pids = []

    def make(template='adult', **changes):
        pid = _member(template, **changes)
        pids.append(pid)
        return pid
    yield make
    for pid in pids:
        try:
            _db().delete_profile(pid)
        except Exception:  # noqa: BLE001
            pass


def _concrete(rule):
    return re.sub(r'<(?:[^:>]+:)?([^>]+)>', lambda m: '1' if 'int:' in m.group(0) else 'x', rule)


def _declared(kind):
    out = []
    for key, access in inventory(APP).items():
        methods, rule = key.split(' ', 1)
        if rule.startswith(STILL_LISTED_PREFIXES) or access is None or access[0] != 'perm' or access[1] != kind:
            continue
        out.append((methods.split(',')[0], rule))
    return sorted(out)


def _refused_with(resp, perm):
    if resp.status_code != 403:
        return False
    body = resp.get_json(silent=True) or {}
    # a page-only prefix answers from the profile gate first (page_not_allowed);
    # everything else is the standard refusal for the row it declares
    return body.get('error') == 'page_not_allowed' or (body.get('error') == 'not_allowed'
                                                         and body.get('permission') == perm)


# ── the check itself ─────────────────────────────────────────────────────


def test_the_real_app_always_enforces_and_a_bare_app_only_with_a_map():
    assert APP.config.get(P.GATE_FLAG) is True
    with APP.test_request_context('/'):
        assert P.enforcing()
    bare = Flask(__name__)
    with bare.test_request_context('/'):
        assert not P.enforcing()
        assert not P.restricted('music.hide_explicit')
        g.perms = member_perms('kids')
        assert P.enforcing() and P.restricted('music.hide_explicit')


def test_a_bare_app_mounting_a_declared_view_keeps_working_without_profiles():
    bare = Flask(__name__)

    @bare.route('/x')
    @P.requires('music.discover')
    def x():
        return 'ok'
    assert bare.test_client().get('/x').status_code == 200


def test_a_row_that_needs_a_side_does_nothing_without_it():
    video_only = member_ctx(**{'music.access': False})
    assert not P.can(video_only, 'music.get') and not P.can(video_only, 'music.discover')
    assert not P.can(video_only, 'music.quality', 3)
    # a restriction never loosens with its side, and ratings keep theirs
    kid_video_only = member_ctx('kids', **{'music.access': False})
    assert P.can(kid_video_only, 'music.hide_explicit')
    assert P.can(member_ctx(**{'movies.access': False, 'tv.access': True}), 'video.search')
    assert not P.can(member_ctx(**{'movies.access': False, 'tv.access': False}), 'video.search')
    assert P.can(member_ctx(), 'music.get', 'download')


# ── every declared route, generated from the inventory ───────────────────

PAGE_ROWS = sorted({r.id for r in P.PAGE_ROWS if not r.id.startswith(('video.', 'admin.video'))}
                   | {'account.connect', 'music.videos'})


@pytest.mark.parametrize('perm', PAGE_ROWS)
def test_a_member_without_a_row_is_refused_on_every_route_that_declares_it(made, perm):
    routes = _declared(perm)
    if not routes:
        pytest.skip('no route declares %s' % perm)
    c = _as(made(**{perm: False}))
    leaked = []
    for m, r in routes:
        resp = c.open(_concrete(r), method=m)
        if not _refused_with(resp, perm):
            leaked.append((m, r, resp.status_code))
    assert leaked == []


def test_a_video_only_member_is_refused_every_music_route(made):
    c = _as(made(**{'music.access': False}))
    routes = [rt for row in P.CATALOGUE_ROWS if row.id == 'music.access' or 'music.access' in row.needs
              for rt in _declared(row.id)]
    assert len(routes) > 400
    leaked = []
    for m, r in routes:
        resp = c.open(_concrete(r), method=m)
        body = resp.get_json(silent=True) or {}
        if resp.status_code != 403 or body.get('error') not in ('not_allowed', 'page_not_allowed'):
            leaked.append((m, r, resp.status_code))
    assert leaked == []


def test_a_member_with_the_row_gets_past_the_gate(made):
    c = _as(made())
    for path in ('/api/discover/layout', '/api/stats/overview', '/api/sample/folders',
                 '/api/watchlist/count', '/api/automations/blocks'):
        resp = c.get(path)
        body = resp.get_json(silent=True) or {}
        assert body.get('error') not in ('not_allowed', 'page_not_allowed'), path


def test_no_profile_on_a_multi_profile_install_is_no_one(made):
    made()
    c = APP.test_client()
    with c.session_transaction() as s:
        s.clear()
    resp = c.get('/api/discover/layout')
    assert resp.status_code in (401, 403)


def test_an_admin_passes_every_row(made):
    made()
    c = _as(1)
    assert c.get('/api/discover/layout').status_code == 200


# ── the profile routes load the map too (account.connect) ────────────────


def test_connecting_an_own_account_needs_account_connect(made):
    off = _as(made(**{'account.connect': False}))
    r = off.post('/api/profiles/me/lastfm', json={'username': 'someone'})
    assert r.status_code == 403 and r.get_json()['permission'] == 'account.connect'
    # reading your own connections and letting one go stay yours
    assert off.get('/api/profiles/me/connections').status_code == 200
    on = _as(made())
    r = on.post('/api/profiles/me/lastfm', json={'username': ''})
    assert (r.get_json(silent=True) or {}).get('error') != 'not_allowed'


# ── S4: a member's own automations are personal ──────────────────────────


def test_a_member_without_their_own_automations_is_refused(made):
    c = _as(made(**{'automations.personal': False}))
    assert c.get('/api/automations').status_code == 403
    assert c.post('/api/automations', json={'name': 'x', 'action_type': 'refresh_mirrored'}).status_code == 403


def test_test_notifications_and_the_script_list_are_the_admins(made):
    c = _as(made())
    r = c.post('/api/automations/test-notify', json={'type': 'webhook', 'config': {'url': 'http://x'}})
    assert r.status_code == 403
    assert c.get('/api/scripts').status_code == 403


class _AutoDb:
    def __init__(self, rows):
        self.rows = {r['id']: r for r in rows}

    def get_automation(self, automation_id):
        return self.rows.get(int(automation_id))


@pytest.fixture
def auto_routes(monkeypatch):
    import api.automations as routes
    caller = {'pid': 2, 'admin': False}
    calls = []

    class _Api:
        def __getattr__(self, name):
            def call(*a, **k):
                calls.append(name)
                return {'success': True}, 200
            return call
    rows = [
        {'id': 20, 'profile_id': 2, 'is_system': 0, 'name': 'pull', 'action_type': 'refresh_mirrored',
         'then_actions': json.dumps([{'type': 'ntfy'}])},
        {'id': 21, 'profile_id': 2, 'is_system': 0, 'name': 'old', 'action_type': 'search_and_download',
         'then_actions': '[]'},
        {'id': 22, 'profile_id': 1, 'is_system': 1, 'name': 'system', 'action_type': 'library_cleanup',
         'then_actions': '[]'},
    ]
    monkeypatch.setattr(routes, '_auto_api', _Api())
    monkeypatch.setattr(routes, 'get_database', lambda: _AutoDb(rows))
    monkeypatch.setattr(routes, 'automation_engine', None)
    monkeypatch.setattr(routes, 'is_admin_request', lambda: caller['admin'])
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: caller['pid'])
    app = Flask(__name__)
    app.secret_key = 't'
    app.register_blueprint(routes.bp)
    return app.test_client(), calls, caller


@pytest.mark.parametrize('body, ok', [
    ({'name': 'a', 'action_type': 'refresh_mirrored'}, True),
    ({'name': 'a', 'action_type': 'scan_watchlist', 'then_actions': [{'type': 'discord_webhook'}]}, True),
    ({'name': 'a', 'action_type': 'sync_playlist', 'owned_by': 'auto_sync'}, True),
    ({'name': 'a', 'action_type': 'search_and_download'}, False),
    ({'name': 'a', 'action_type': 'library_cleanup'}, False),
    ({'name': 'a', 'action_type': 'video_purge_recycle_bin'}, False),
    ({'name': 'a', 'action_type': 'refresh_mirrored', 'then_actions': [{'type': 'fire_signal'}]}, False),
    ({'name': 'a', 'action_type': 'refresh_mirrored', 'notify_type': 'run_script'}, False),
    ({'name': 'a', 'action_type': 'refresh_mirrored', 'owned_by': 'video'}, False),
    ({'name': 'a', 'action_type': 'refresh_mirrored', 'owned_by': 'system:repair_job'}, False),
    ({'name': 'a', 'action_type': 'refresh_mirrored', 'then_actions': 'garbage'}, False),
])
def test_a_member_saves_only_personal_automations(auto_routes, body, ok):
    http, calls, _caller = auto_routes
    r = http.post('/api/automations', json=body)
    assert r.status_code == (200 if ok else 403), r.get_data(as_text=True)
    assert (calls == ['create_automation']) is ok


def test_an_admin_saves_any_action_but_not_a_system_owner(auto_routes):
    http, calls, caller = auto_routes
    caller['admin'] = True
    assert http.post('/api/automations', json={'name': 'a', 'action_type': 'library_cleanup',
                                               'owned_by': 'video'}).status_code == 200
    assert http.post('/api/automations', json={'name': 'a', 'action_type': 'library_cleanup',
                                               'owned_by': 'system:repair_job'}).status_code == 403


def test_a_member_runs_their_own_personal_automation_only(auto_routes):
    http, calls, _caller = auto_routes
    assert http.post('/api/automations/20/run').status_code == 200
    assert http.post('/api/automations/21/run').status_code == 403      # saved before the rule
    assert http.post('/api/automations/22/run').status_code == 403      # the system's
    assert http.post('/api/automations/21/duplicate').status_code == 403
    assert calls == ['run_automation']


def test_a_member_tidies_an_old_automation_but_cant_widen_one(auto_routes):
    http, calls, _caller = auto_routes
    assert http.put('/api/automations/21', json={'name': 'renamed'}).status_code == 200
    assert http.put('/api/automations/20', json={'action_type': 'run_repair_job'}).status_code == 403
    assert http.put('/api/automations/20', json={'then_actions': [{'type': 'fire_signal'}]}).status_code == 403
    assert http.put('/api/automations/20', json={'then_actions': [{'type': 'gotify'}]}).status_code == 200
    assert http.put('/api/automations/21', json={'action_type': 'refresh_mirrored'}).status_code == 200


def test_the_engine_skips_a_members_automation_that_isnt_personal(made):
    from core.automation_engine import AutomationEngine
    eng = AutomationEngine.__new__(AutomationEngine)
    eng.db = _db()
    member = made()
    no_autos = made(**{'automations.personal': False})
    personal = {'id': 1, 'profile_id': member, 'is_system': 0, 'action_type': 'refresh_mirrored',
                'then_actions': '[]'}
    assert eng.personal_refusal(personal) is None
    assert eng.personal_refusal(dict(personal, action_type='search_and_download'))
    assert eng.personal_refusal(dict(personal, then_actions=json.dumps([{'type': 'fire_signal'}])))
    assert eng.personal_refusal(dict(personal, profile_id=no_autos))
    assert eng.personal_refusal(dict(personal, profile_id=999999))       # gone: can't be read
    assert eng.personal_refusal(dict(personal, is_system=1, action_type='library_cleanup')) is None
    assert eng.personal_refusal(dict(personal, profile_id=1, action_type='library_cleanup')) is None


class _EngineDb:
    """the real test database, with one automation row of the test's."""
    def __init__(self, row):
        self.row = row

    def get_automation(self, automation_id):
        return dict(self.row)

    def __getattr__(self, name):
        return getattr(_db(), name)


def _engine(row, handled):
    from core.automation_engine import AutomationEngine
    eng = AutomationEngine.__new__(AutomationEngine)
    eng.db = _EngineDb(row)
    eng._running = True
    eng._migration_paused = False
    eng._default_tz = None
    eng._progress_init_fn = eng._progress_update_fn = eng._progress_finish_fn = None
    eng._history_record_fn = None
    eng._action_handlers = {row['action_type']: {'handler': lambda cfg: handled.append(cfg) or {'status': 'ok'}}}
    eng._execute_then_actions = lambda *a, **k: None
    finished = []
    eng._finish_run = lambda auto, aid, result, error=None, **k: finished.append(result)
    eng.update_automation_run = lambda *a, **k: None
    return eng, finished


def test_a_members_old_automation_never_runs_on_schedule_or_on_an_event(made):
    member = made()
    row = {'id': 77, 'name': 'old', 'profile_id': member, 'is_system': 0, 'enabled': 1,
           'action_type': 'search_and_download', 'action_config': '{}', 'then_actions': '[]',
           'trigger_type': 'schedule'}
    handled = []
    eng, finished = _engine(row, handled)
    eng.run_automation(77, skip_delay=True)
    assert handled == [] and finished and finished[0]['status'] == 'skipped'
    eng.db.update_automation_run = lambda *a, **k: None
    eng._run_event_automation(dict(row), 77, {})
    assert handled == []


def test_a_members_personal_automation_runs_for_them_only(made):
    member = made()
    row = {'id': 78, 'name': 'scan', 'profile_id': member, 'is_system': 0, 'enabled': 1,
           'action_type': 'scan_watchlist', 'action_config': '{}', 'then_actions': '[]',
           'trigger_type': 'schedule'}
    handled = []
    eng, _finished = _engine(row, handled)
    eng.run_automation(78, skip_delay=True)
    # a scheduled scan used to pass no profile: "every profile's watchlist"
    assert handled and handled[0]['_profile_id'] == member


# ── music.get, requests.limit and the wishlist ───────────────────────────


def test_music_rights_follow_the_map_and_the_policy(made):
    db = _db()
    asks = made('teen', **{'requests.limit': {'n': 3, 'days': 7}})
    rights = PS.music_rights(db, db.get_profile(asks))
    assert rights == {'mode': 'ask', 'quota': {'limit': 3, 'days': 7}, 'hide_explicit': False}
    downloads = made()
    assert PS.music_rights(db, db.get_profile(downloads))['mode'] == 'download'
    none = made(**{'music.get': 'none'})
    assert PS.music_rights(db, db.get_profile(none))['mode'] == 'none'
    video_only = made(**{'music.access': False})
    assert PS.music_rights(db, db.get_profile(video_only))['mode'] == 'none'
    assert PS.music_rights(db, db.get_profile(1)) == {'mode': 'download', 'quota': None, 'hide_explicit': False}
    assert PS.music_rights(db, None)['mode'] == 'none'


def _track(tid, explicit=None, album=None):
    return {'id': tid, 'name': 'song %s' % tid, 'explicit': explicit, 'artists': [{'name': 'A'}],
            'album': {'id': album or 'alb' + tid, 'name': 'Al', 'album_type': 'album', 'images': []}}


def test_the_wishlist_holds_every_add_to_the_owners_rights(made):
    db = _db()
    kid = made('kids')
    tag = uuid4().hex[:6]
    try:
        out = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + 'e', True), profile_id=kid,
                                          source_type='manual', user_initiated=True)
        assert out['status'] == 'rejected' and out['reason'] == PS.WISH_EXPLICIT
        out = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + 'c', False), profile_id=kid,
                                          source_type='manual', user_initiated=True)
        assert out['created']
        none = made(**{'music.get': 'none'})
        out = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + 'n'), profile_id=none,
                                          source_type='manual', user_initiated=True)
        assert out['status'] == 'rejected' and out['reason'] == PS.WISH_NOT_ALLOWED
        # an admin's add for them (the library v2 mirror) is the admin's
        out = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + 'm', True), profile_id=kid,
                                          source_type='manual', request_approved=True)
        assert out['created']
    finally:
        db.clear_wishlist(profile_id=kid)


def test_the_request_limit_comes_from_the_permission_map(made):
    db = _db()
    pid = made('teen', **{'requests.limit': {'n': 1, 'days': 7}})
    tag = uuid4().hex[:6]
    try:
        first = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + '1', album='x' + tag), profile_id=pid,
                                            source_type='album', user_initiated=True)
        second = db.add_to_wishlist_detailed(spotify_track_data=_track(tag + '2', album='y' + tag), profile_id=pid,
                                             source_type='album', user_initiated=True)
        assert first['created']
        assert second['status'] == 'rejected' and second['reason'] == 'request_limit'
    finally:
        db.clear_wishlist(profile_id=pid)


def test_a_member_who_asks_first_cant_start_a_music_download(made):
    c = _as(made('teen'))
    r = c.post('/api/download', json={'username': 'u', 'filename': 'f.flac'})
    assert r.status_code == 403
    assert r.get_json()['error'] in ('not_allowed', 'Downloads are disabled for this profile.')


def test_a_policy_cap_holds_a_member_still_on_their_own_columns():
    """a member on its old columns (no template) whose column says download,
    under a policy that caps music at ask: the music download check reads the
    map, so the cap holds."""
    db = _db()
    pid = db.create_profile(name='legacy_%s' % uuid4().hex[:8], can_download=True)
    try:
        PS.change_policy(db, {'set': {'music.get': 'ask'}})
        assert db.get_profile(pid)['can_download']           # its column never moved
        r = _as(pid).post('/api/download', json={'username': 'u', 'filename': 'f.flac'})
        assert r.status_code == 403 and r.get_json()['permission'] == 'music.get'
    finally:
        PS.change_policy(db, {'reset': 'all'})
        db.delete_profile(pid)


def test_books_and_podcasts_answer_to_their_own_mode(made):
    c = _as(made(**{'books.get': 'none', 'podcasts.get': 'none'}))
    r = c.post('/api/audiobooks/wishlist', json={'asin': 'B000000001', 'title': 'x'})
    assert r.status_code == 403 and r.get_json()['permission'] == 'books.get'
    r = c.post('/api/podcasts/download', json={})
    assert r.status_code == 403 and r.get_json()['permission'] == 'podcasts.get'


# ── moderators ───────────────────────────────────────────────────────────


@pytest.fixture
def waiting(made):
    """a member who asks first, with one album request waiting."""
    db = _db()
    pid = made('teen')
    tag = uuid4().hex[:6]
    for tid in ('a', 'b'):
        db.add_to_wishlist(spotify_track_data=_track(tag + tid, album='alb' + tag), source_type='album',
                           profile_id=pid, user_initiated=True)
    yield pid
    db.clear_wishlist(profile_id=pid)


def _waiting_key(client, owner):
    body = client.get('/api/requests/music?status=pending').get_json()
    return next(g['key'] for g in body['pending'] if g['profile_id'] == owner)


def test_a_moderator_sees_and_answers_other_peoples_requests(made, waiting, monkeypatch):
    import core.profile_notify as pn
    monkeypatch.setattr(pn, '_journal', lambda *a: None)
    monkeypatch.setattr(pn, '_emitter', None)
    mod = _as(made(**{'requests.moderate': True}))
    listing = mod.get('/api/requests/music?status=pending').get_json()
    assert listing['moderator'] is True
    key = _waiting_key(mod, waiting)
    assert mod.get('/api/requests/music/counts').get_json()['pending'] >= 1
    r = mod.post('/api/requests/music/approve', json={'profile_id': waiting, 'key': key})
    assert r.status_code == 200 and r.get_json()['approved'] == 2


def test_a_moderator_never_answers_their_own(made):
    db = _db()
    pid = made('teen', **{'requests.moderate': True})
    tag = uuid4().hex[:6]
    db.add_to_wishlist(spotify_track_data=_track(tag), source_type='manual', profile_id=pid, user_initiated=True)
    try:
        c = _as(pid)
        key = _waiting_key(c, pid)
        r = c.post('/api/requests/music/approve', json={'profile_id': pid, 'key': key})
        assert r.status_code == 403 and r.get_json()['permission'] == 'requests.moderate'
        assert c.post('/api/requests/music/approve-all', json={'profile_id': pid}).status_code == 403
    finally:
        db.clear_wishlist(profile_id=pid)


def test_a_member_neither_sees_nor_answers_others(made, waiting):
    c = _as(made())
    assert all(g['profile_id'] != waiting
               for g in c.get('/api/requests/music?status=pending').get_json()['pending'])
    assert c.post('/api/requests/music/decline', json={'profile_id': waiting, 'key': 'x'}).status_code == 403
    assert c.post('/api/requests/music/approve-all', json={}).status_code == 403


def test_see_all_reads_everyones_but_answers_none(made, waiting):
    c = _as(made(**{'requests.see_all': True}))
    key = _waiting_key(c, waiting)
    assert c.post('/api/requests/music/approve', json={'profile_id': waiting, 'key': key}).status_code == 403


def test_a_moderator_manages_issues_but_never_settings_people_or_tools(made):
    mod = _as(made(**{'requests.moderate': True}))
    reporter = _as(made())
    r = reporter.post('/api/issues', json={'entity_type': 'album', 'entity_id': uuid4().hex,
                                           'category': 'other', 'title': 'wrong cover'})
    issue_id = r.get_json().get('id')
    assert issue_id
    r = mod.put('/api/issues/%s' % issue_id, json={'status': 'resolved', 'admin_response': 'fixed'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert mod.get('/api/issues/%s' % issue_id).status_code == 200
    plain = _as(made())
    assert plain.get('/api/issues/%s' % issue_id).status_code == 404
    for method, path in (('POST', '/api/test-connection'), ('POST', '/api/profiles'),
                         ('POST', '/api/repair/jobs/x/run'), ('GET', '/api/permissions/policy')):
        assert mod.open(path, method=method, json={}).status_code == 403, path


# ── S17: hide explicit on the lists the content guard didn't cover ───────


def _kid_request(path, monkeypatch, explicit_ids, method='GET', **kw):
    import api.content_guard as cg
    monkeypatch.setattr('core.library2.explicit.library_track_is_explicit',
                        lambda conn, file_path=None, lib2_track_id=None, track_id=None:
                        str(track_id) in explicit_ids or str(lib2_track_id) in explicit_ids)
    ctx = APP.test_request_context(path, method=method, **kw)
    ctx.push()
    g.profile_id, g.is_admin, g.perms = 50, False, member_perms('kids')
    return cg, ctx


@pytest.mark.parametrize('path, shape', [
    ('/api/library/radio', lambda ts: {'success': True, 'tracks': ts}),
    ('/api/library/search-tracks', lambda ts: {'success': True, 'tracks': ts}),
    ('/api/library/tracks', lambda ts: {'success': True, 'data': {'tracks': ts}}),
    ('/api/library/tracks/recent', lambda ts: {'success': True, 'data': {'tracks': ts}}),
    ('/api/discover/weekly', lambda ts: {'success': True, 'tracks': ts}),
    ('/api/stats/top-tracks', lambda ts: {'success': True, 'tracks': ts}),
])
def test_a_kid_never_gets_an_explicit_track_in_a_list(monkeypatch, path, shape):
    tracks = [{'id': 1, 'title': 'clean'}, {'id': 2, 'title': 'lookup says explicit'},
              {'id': 3, 'title': 'flagged', 'explicit': True}]
    cg, ctx = _kid_request(path, monkeypatch, {'2'})
    try:
        from flask import jsonify
        out = cg._filter_response(jsonify(shape(tracks)))
        body = out.get_json()
        flat = json.dumps(body)
        assert 'clean' in flat and 'flagged' not in flat
        if path.startswith('/api/library'):
            assert 'lookup says explicit' not in flat
    finally:
        ctx.pop()


def test_an_adult_gets_the_list_untouched(monkeypatch):
    import api.content_guard as cg
    from flask import jsonify
    with APP.test_request_context('/api/library/radio'):
        g.profile_id, g.is_admin, g.perms = 50, False, member_perms('adult')
        g._content_restrictions = {'hide_explicit': False, 'cap': None}
        out = cg._filter_response(jsonify({'tracks': [{'id': 3, 'title': 'flagged', 'explicit': True}]}))
        assert 'flagged' in out.get_data(as_text=True)


def test_a_policy_forced_restriction_hides_even_with_a_clean_column(monkeypatch):
    """a profile on its own columns (hide_explicit 0) under a policy that
    forces the restriction on: the map says hide, so it's hidden."""
    import api.content_guard as cg
    with APP.test_request_context('/api/library/radio'):
        g.profile_id, g.is_admin = 50, False
        g.perms = member_perms('adult', policy={'music.hide_explicit': True})
        g._content_restrictions = {'hide_explicit': False, 'cap': None}
        assert cg._hide_explicit()


def test_sample_studio_wont_open_an_explicit_track_for_a_kid(monkeypatch):
    cg, ctx = _kid_request('/api/sample/peaks?track_id=7', monkeypatch, {'7'})
    try:
        monkeypatch.setattr(cg, '_get_database', lambda: _db())
        out = cg._guard_play()
        assert out is not None and out[1] == 403
    finally:
        ctx.pop()
    cg, ctx = _kid_request('/api/sample/peaks?track_id=8', monkeypatch, {'7'})
    try:
        monkeypatch.setattr(cg, '_get_database', lambda: _db())
        assert cg._guard_play() is None
    finally:
        ctx.pop()


def test_a_kid_cant_fetch_or_ask_for_a_music_video(made, monkeypatch):
    cg, ctx = _kid_request('/api/music-video/download', monkeypatch, set(), method='POST', json={})
    try:
        out = cg._guard_play()
        assert out is not None and out[1] == 403
    finally:
        ctx.pop()
    kid = _as(made('kids'))
    r = kid.post('/api/requests/music/videos', json={'video_id': 'v', 'url': 'u', 'title': 't'})
    assert r.status_code == 403 and r.get_json().get('restricted') is True


def test_a_member_who_cant_request_music_cant_ask_for_a_video(made):
    c = _as(made(**{'music.get': 'none'}))
    r = c.post('/api/requests/music/videos', json={'video_id': 'v', 'url': 'u', 'title': 't'})
    assert r.status_code == 403 and r.get_json()['permission'] == 'music.get'
