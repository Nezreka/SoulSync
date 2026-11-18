"""the people and permissions follow-ups (#2166, #2167, #2168): the gaps the
pieces left, each held here with the real app and the shared test database.

- auto-sync schedules follow Playlist sync, not "Their own automations" (23)
- a member's event automations hear only their own events (25), and their
  notes go to public addresses only (26)
- the wishlist scheduler and books / podcasts read their own rows (24, 14)
- sign-ups follow the sign-up template, the old settings moved there (7)
- a template's Sidebar & look is what its followers see (8)
- the admin switch rides the permissions PUT (4), sign-in method (1)
- quality options carry a range line and the resolver's own tags (3, 19)
- podcasts read "Follow only", the summary speaks to "you" (6, 16)
- one count of asks for music and video, and the admin's per-person view (15, 10)
"""

from __future__ import annotations

import json
import os
import tempfile
from uuid import uuid4

import pytest
from flask import Flask

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-permfu-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'permfu.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402
from core import permissions as P  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


def _member(template='adult', **changes):
    db = _db()
    pid = db.create_profile(name='fu_%s' % uuid4().hex[:8])
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


@pytest.fixture(autouse=True)
def _open_policy():
    _db().set_permission_policy_raw('{}')
    yield
    _db().set_permission_policy_raw('{}')


# ── 23: auto-sync schedules follow Playlist sync ─────────────────────────


def _automation(pid, owned_by='', action='sync_playlist'):
    db = _db()
    return db.create_automation(name='a_%s' % uuid4().hex[:6], trigger_type='schedule',
                                trigger_config='{}', action_type=action, action_config='{}',
                                profile_id=pid, owned_by=owned_by)


def test_playlist_sync_alone_reaches_the_auto_sync_schedules_and_nothing_else(made):
    pid = made(**{'automations.personal': False})
    sync_row = _automation(pid, owned_by='auto_sync')
    own_row = _automation(pid, action='refresh_mirrored')
    c = _as(pid)
    listed = c.get('/api/automations')
    assert listed.status_code == 200
    ids = {a['id'] for a in listed.get_json()}
    assert sync_row in ids and own_row not in ids
    assert c.get('/api/automations/%d' % sync_row).status_code == 200
    assert c.post('/api/automations/%d/toggle' % sync_row).status_code != 403
    refused = c.get('/api/automations/%d' % own_row)
    assert refused.status_code == 403 and refused.get_json()['permission'] == 'automations.personal'
    # a new auto-sync schedule is theirs to make, anything else isn't
    assert c.post('/api/automations', json={'name': 's', 'action_type': 'sync_playlist',
                                            'owned_by': 'auto_sync'}).status_code != 403
    assert c.post('/api/automations', json={'name': 'x', 'action_type': 'refresh_mirrored'}).status_code == 403
    # and a schedule can't be moved out of auto-sync into an automation
    moved = c.put('/api/automations/%d' % sync_row, json={'owned_by': ''})
    assert moved.status_code == 403


def test_without_playlist_sync_the_auto_sync_schedules_go_even_with_automations(made):
    pid = made(**{'music.playlist_sync': False})
    sync_row = _automation(pid, owned_by='auto_sync')
    own_row = _automation(pid, action='refresh_mirrored')
    c = _as(pid)
    ids = {a['id'] for a in c.get('/api/automations').get_json()}
    assert own_row in ids and sync_row not in ids
    refused = c.post('/api/automations/%d/run' % sync_row)
    assert refused.status_code == 403 and refused.get_json()['permission'] == 'music.playlist_sync'


def test_neither_row_is_refused_at_the_door(made):
    pid = made(**{'automations.personal': False, 'music.playlist_sync': False})
    r = _as(pid).get('/api/automations')
    assert r.status_code == 403 and r.get_json()['permission'] == 'automations.personal'


def test_the_engine_runs_an_auto_sync_schedule_by_playlist_sync(made):
    from core.automation_engine import AutomationEngine
    eng = AutomationEngine.__new__(AutomationEngine)
    eng.db = _db()
    row = {'id': 1, 'is_system': 0, 'action_type': 'sync_playlist', 'owned_by': 'auto_sync',
           'then_actions': '[]'}
    assert eng.personal_refusal(dict(row, profile_id=made(**{'automations.personal': False}))) is None
    assert eng.personal_refusal(dict(row, profile_id=made(**{'music.playlist_sync': False})))


def test_requires_any_declares_and_refuses_with_the_first_row():
    view = P.requires_any('automations.personal', 'music.playlist_sync')(lambda: 'ok')
    assert P.declared_access(view) == ('perm_any', ('automations.personal', 'music.playlist_sync'))
    with pytest.raises(ValueError):
        P.requires_any('no.such.row')
    with pytest.raises(ValueError):
        P.requires_any('ui.sidebar')


# ── 25 + 26: a member's automations, events and notes ───────────────────


def _engine():
    from core.automation_engine import AutomationEngine
    eng = AutomationEngine.__new__(AutomationEngine)
    eng.db = _db()
    return eng


def test_a_members_event_automation_hears_only_their_own_events(made):
    eng = _engine()
    member = made()
    row = {'id': 5, 'profile_id': member, 'is_system': 0, 'trigger_type': 'playlist_synced'}
    assert eng.event_refusal(row, {})                                   # the install's event
    assert eng.event_refusal(row, {'_event_owner': member + 1000})      # someone else's
    assert eng.event_refusal(row, {'_event_owner': member}) is None     # theirs
    assert eng.event_refusal(dict(row, trigger_type='app_started'), {}) is None
    # an admin's and the system's hear everything, as before
    assert eng.event_refusal(dict(row, profile_id=1), {}) is None
    assert eng.event_refusal(dict(row, is_system=1), {}) is None


def test_emit_stamps_the_profile_the_work_runs_for():
    from core.automation_engine import AutomationEngine
    from core.profile_context import reset_background_profile, set_background_profile
    eng = AutomationEngine.__new__(AutomationEngine)
    eng._running = True
    seen = []
    eng._process_event = lambda etype, data: seen.append(data)
    import threading
    started = []

    class _T:
        def __init__(self, target, args, **k):
            self.target, self.args = target, args

        def start(self):
            started.append(self)
            self.target(*self.args)
    orig = threading.Thread
    threading.Thread = _T
    try:
        token = set_background_profile(42)
        try:
            eng.emit('playlist_synced', {'playlist_name': 'x'})
        finally:
            reset_background_profile(token)
        eng.emit('batch_complete', {})
    finally:
        threading.Thread = orig
    assert seen[0]['_event_owner'] == 42
    assert '_event_owner' not in seen[1]


def test_a_members_notes_go_to_public_addresses_only(made, monkeypatch):
    import core.automation_engine as ae
    import core.podcast_ingest_guard as guard
    posted = []

    class _Resp:
        status_code = 200
        text = ''

    monkeypatch.setattr(ae.requests, 'post', lambda url, **k: posted.append((url, k)) or _Resp())
    monkeypatch.setattr(guard, 'check_url', lambda url: (url.startswith('https://public.'), 'private'))
    eng = _engine()
    member = made()
    steps = json.dumps([{'type': 'webhook', 'config': {'url': 'http://192.168.1.10/hook'}},
                        {'type': 'discord_webhook', 'config': {'webhook_url': 'https://public.example/x'}}])
    eng._execute_then_actions({'id': 9, 'profile_id': member, 'is_system': 0, 'then_actions': steps},
                              {'status': 'ok'})
    assert [u for u, _k in posted] == ['https://public.example/x']
    assert posted[0][1]['allow_redirects'] is False
    # an admin's automation sends where it's told, as before
    posted.clear()
    eng._execute_then_actions({'id': 10, 'profile_id': 1, 'is_system': 0, 'then_actions': steps},
                              {'status': 'ok'})
    assert [u for u, _k in posted] == ['http://192.168.1.10/hook', 'https://public.example/x']
    assert 'allow_redirects' not in posted[0][1]


# ── 24 + 14: the scheduler and books / podcasts read their own rows ─────


def test_the_wishlist_scheduler_reads_music_get_under_the_policy(made):
    from core.library2.mirror_outbox import music_asks_first, profile_asks_first
    db = _db()
    downloads = made()
    assert music_asks_first(db, db.get_profile(downloads)) is False
    # a policy cap holds even while the cached column still says download
    db.set_permission_policy_raw(json.dumps({'music.get': 'ask'}))
    prof = db.get_profile(downloads)
    assert music_asks_first(db, dict(prof, can_download=True)) is True
    assert profile_asks_first(db, downloads) is True
    assert profile_asks_first(db, 1) is False


def test_books_and_podcasts_follow_their_own_rows_not_music(made, monkeypatch):
    from api.helpers import media_download_error
    member = made(**{'music.get': 'ask'})       # music asks first; books and podcasts download
    no_books = made(**{'books.get': 'none'})
    assert P.owner_may_get(member, 'books.get') is True
    assert P.owner_may_get(member, 'podcasts.get') is True
    assert P.owner_may_get(no_books, 'books.get') is False
    assert P.owner_may_get(no_books, 'podcasts.get') is True
    assert P.owner_may_get(1, 'books.get') is True
    assert P.owner_may_get(999999, 'books.get') is False      # gone: fails closed
    # the routes: the request's map decides, never the music switch
    app = Flask(__name__)
    with app.test_request_context('/'):
        from flask import g
        g.perms = PS.context_for(_db(), member)['perms']
        assert media_download_error('books.get') is None
        g.perms = PS.context_for(_db(), no_books)['perms']
        resp, status = media_download_error('books.get')
        assert status == 403 and resp.get_json()['permission'] == 'books.get'
        g.perms = None                                         # couldn't build it
        assert media_download_error('podcasts.get') is not None


# ── 7: sign-ups follow the sign-up template ──────────────────────────────


@pytest.fixture
def fresh_signup():
    db = _db()
    keys = (PS.SIGNUP_MOVED_KEY, 'perm_signup_template')
    saved = {k: db.get_metadata(k) for k in keys}
    guest = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'guest')
    # start clean: an earlier test's sign-in can leave a "New sign-ups" template
    for t in db.list_permission_templates():
        if t['name'] == PS.SIGNUP_TEMPLATE_NAME:
            db.delete_permission_template(int(t['id']), reassign_to=guest)
    if saved.get('perm_signup_template') and not db.get_permission_template(int(saved['perm_signup_template'])):
        saved['perm_signup_template'] = str(guest)
    db.set_metadata('perm_signup_template', str(guest))
    db.delete_metadata(PS.SIGNUP_MOVED_KEY)
    yield db, guest
    for t in db.list_permission_templates():
        if t['name'] == PS.SIGNUP_TEMPLATE_NAME and not t.get('followers'):
            db.delete_permission_template(int(t['id']))
    for k, v in saved.items():
        if v is None:
            db.delete_metadata(k)
        else:
            db.set_metadata(k, v)


def _settings(**values):
    return lambda key, default=None: values.get(key, default)


def test_the_old_signup_settings_become_the_signup_template_once(fresh_signup):
    db, guest = fresh_signup
    get = _settings(**{'security.signup_sides': 'both', 'security.signup_request_limit': 3,
                       'security.plex_signin': True, 'security.plex_signin_default_can_download': True})
    tid = PS.move_signup_settings(db, get)
    assert tid != guest and PS.signup_template_id(db) == tid
    row = db.get_permission_template(tid)
    assert row['name'] == PS.SIGNUP_TEMPLATE_NAME
    perms = json.loads(row['perms'])
    assert perms['movies.access'] is True and perms['music.access'] is True
    # the one old sign-up limit becomes each kind's (music, movies, tv)
    for row in P.LIMIT_ROWS.values():
        assert perms[row] == {'n': 3, 'days': 7}, row
    assert perms['music.get'] == 'download' and perms['books.get'] == 'download'
    assert perms['music.playlist_sync'] is False             # sign-ups never got the machinery
    # once: a later change to the settings doesn't move anything again
    assert PS.move_signup_settings(db, _settings()) == tid


def test_downloads_only_when_every_sign_in_method_said_yes(fresh_signup):
    db, _guest = fresh_signup
    get = _settings(**{'security.plex_signin': True, 'security.plex_signin_default_can_download': True,
                       'security.oidc_signin': True})
    guest = P.clean_layer(P.BUILTIN_TEMPLATES[3]['perms'], fill=P.TEMPLATE_DEFAULTS)
    assert PS.signup_settings_perms(get, guest)['music.get'] == 'ask'


def test_an_install_that_never_used_signup_keeps_guest(fresh_signup):
    db, guest = fresh_signup
    assert PS.move_signup_settings(db, _settings()) == guest
    assert not any(t['name'] == PS.SIGNUP_TEMPLATE_NAME for t in db.list_permission_templates())
    # a plex sign-in that makes profiles was giving the shipped defaults: those move
    db.delete_metadata(PS.SIGNUP_MOVED_KEY)
    tid = PS.move_signup_settings(db, _settings(**{'security.plex_signin': True}))
    assert tid != guest
    assert json.loads(db.get_permission_template(tid)['perms'])['music.import'] is False


def test_an_admins_own_signup_pick_is_kept(fresh_signup):
    db, _guest = fresh_signup
    teen = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'teen')
    db.set_metadata('perm_signup_template', str(teen))
    assert PS.move_signup_settings(db, _settings(**{'security.signup_sides': 'both'})) == teen


def test_a_new_signup_profile_follows_the_signup_template(fresh_signup):
    from core.security.signup_defaults import SignupAccess, create_signup_profile
    db, _guest = fresh_signup
    kids = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'kids')
    db.set_metadata('perm_signup_template', str(kids))
    PS.move_signup_settings(db, _settings())
    pid = create_signup_profile(db, 'su_%s' % uuid4().hex[:6], SignupAccess(), can_download=True)
    try:
        inputs = db.get_permission_inputs(pid)
        assert inputs['template_id'] == kids and json.loads(inputs['overrides'] or '{}') == {}
        prof = db.get_profile(pid)
        assert prof['hide_explicit'] and prof['max_rating'] == 'PG' and not prof['can_download']
    finally:
        db.delete_profile(pid)


# ── 8: a template's Sidebar & look reaches its followers ────────────────


def test_followers_see_their_templates_sidebar(made):
    from core import ui_features
    db = _db()
    pid = made('teen')
    teen = next(t for t in db.list_permission_templates() if t['slug'] == 'teen')
    before = teen.get('sidebar')
    try:
        db.update_permission_template(int(teen['id']), sidebar=json.dumps({'off': ['podcasts'], 'locked': True}))
        prof = db.get_profile(pid)
        assert ui_features.load_off(db, pid, prof) == ['podcasts']
        assert ui_features.is_locked(db, pid, prof) is True
        payload = ui_features.admin_payload(db, pid)
        assert payload['default']['template'] == 'Teen'
        # an admin's own set for the person still wins
        ui_features.save_admin_off(db, pid, ['audiobooks'])
        assert ui_features.load_off(db, pid, db.get_profile(pid)) == ['audiobooks']
    finally:
        db.update_permission_template(int(teen['id']), sidebar=before)
        ui_features.reset(db, pid)


# ── 4 + 1: the admin switch in the same call, and the sign-in method ────


def test_the_admin_switch_rides_the_permissions_put(made):
    db = _db()
    pid = made('teen', **{'music.get': 'download'})
    admin = _as(1)
    up = admin.put('/api/profiles/%d/permissions' % pid, json={'is_admin': True, 'set': {}, 'reset': []})
    assert up.status_code == 200 and up.get_json()['mode'] == 'admin'
    assert db.get_profile(pid)['is_admin'] is True
    guest = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'guest')
    down = admin.put('/api/profiles/%d/permissions' % pid,
                     json={'is_admin': False, 'template_id': guest, 'reset': 'all', 'set': {}})
    body = down.get_json()
    assert down.status_code == 200 and body['mode'] == 'template' and body['template']['id'] == guest
    assert db.get_profile(pid)['is_admin'] is False


def test_a_failed_demotion_leaves_them_an_admin(made):
    db = _db()
    pid = made()
    db.update_profile(pid, is_admin=1)
    r = _as(1).put('/api/profiles/%d/permissions' % pid,
                   json={'is_admin': False, 'template_id': 999999, 'reset': 'all', 'set': {}})
    assert r.status_code == 404
    assert db.get_profile(pid)['is_admin'] is True


def test_the_owner_stays_an_admin_and_is_admin_must_be_a_bool(made):
    r = _as(1).put('/api/profiles/1/permissions', json={'is_admin': False})
    assert r.status_code == 409 and r.get_json()['error'] == 'owner_admin'
    pid = made()
    r = _as(1).put('/api/profiles/%d/permissions' % pid, json={'is_admin': 'yes'})
    assert r.status_code == 400


def test_a_member_cant_make_themselves_an_admin(made):
    pid = made()
    assert _as(pid).put('/api/profiles/%d/permissions' % pid, json={'is_admin': True}).status_code == 403
    assert _db().get_profile(pid)['is_admin'] is False


def test_profiles_say_how_they_sign_in(made):
    db = _db()
    pid = made()
    assert db.get_profile(pid)['signin'] == 'picker'
    db.set_profile_plex_account(pid, 'plex-%s' % uuid4().hex[:6])
    assert db.get_profile(pid)['signin'] == 'plex'
    assert next(p for p in db.get_all_profiles() if p['id'] == pid)['signin'] == 'plex'


# ── 3, 19: quality options ───────────────────────────────────────────────


def test_a_quality_profile_says_what_it_fetches():
    from core.video.quality_profile import default_profile, profile_range
    prof = default_profile()
    assert profile_range(prof) == '720p to 1080p'
    for t in prof['tiers']:
        t['enabled'] = t['key'] in ('remux-2160p', 'dvd')
    assert profile_range(prof) == 'SD to 4K'
    for t in prof['tiers']:
        t['enabled'] = t['key'] == 'web-1080p'
    assert profile_range(prof) == '1080p'
    for t in prof['tiers']:
        t['enabled'] = False
    assert profile_range(prof) == ''


def test_quality_options_route_uses_the_resolvers_list(monkeypatch):
    import api.permissions_routes as routes

    class _Vdb:
        pass
    monkeypatch.setattr(routes, 'get_video_db', lambda: _Vdb())
    import core.video.quality_profile as qp
    monkeypatch.setattr(qp, 'quality_options', lambda db: [
        {'id': 0, 'name': 'Default', 'media_type': None, 'tags': [], 'range': '720p to 1080p'},
        {'id': 3, 'name': 'Movie 4K', 'media_type': 'movie', 'tags': ['4k'], 'range': '4K'},
        {'id': 5, 'name': 'TV', 'media_type': 'tv', 'tags': [], 'range': '720p'}])
    body = _as(1).get('/api/permissions/quality-options').get_json()
    assert [o['id'] for o in body['movies']] == [0, 3]
    assert [o['id'] for o in body['tv']] == [0, 5]
    assert body['movies'][1]['tags'] == ['4k'] and body['movies'][1]['range'] == '4K'


# ── 6, 16: words ─────────────────────────────────────────────────────────


def test_podcasts_read_follow_only_and_the_summary_speaks_to_you():
    rows = {r['id']: r for r in P.catalogue_payload()['rows']}
    assert rows['podcasts.get']['value_labels']['none'] == 'Follow only'
    assert rows['music.get']['value_labels']['none'] == "Can't request"
    eff = P.effective(None, P.BUILTIN_TEMPLATES[1]['perms'], {'automations.personal': False}, {'id': 9})
    you = P.summarize(eff, name='You')
    assert any(line['text'].startswith('Podcasts:') for line in you['lines'])
    assert 'Your own automations' in you['never']
    assert any('you ask first' in line['text'] for line in you['lines'])
    them = P.summarize(eff, name='Maya')
    assert 'Their own automations' in them['never']


# ── 15, 10: each kind counts its own asks ────────────────────────────────


class _VideoCount:
    """a video db that answers per request kind: {kind: n} (an unknown
    kind, a log row from before v75, is under None)."""

    def __init__(self, by_kind):
        self.by_kind = by_kind
        self.asked = []

    def count_video_requests_since(self, pid, days, kinds=None):
        self.asked.append(tuple(kinds) if kinds is not None else None)
        if self.by_kind is None:
            return None
        ks = list(self.by_kind) if kinds is None else list(kinds) + [None]
        return sum(n for k, n in self.by_kind.items() if k in ks)


def test_each_kind_counts_only_its_own_asks(made):
    from core.requests.asks import asks_used
    db = _db()
    pid = made()
    v = _VideoCount({'movie': 2, 'show': 1, 'episode': 3, 'youtube': 4})
    both = {'movies.access': True, 'tv.access': True}
    assert asks_used(pid, 7, 'movies', perms=both, music_db=db, video_db=v) == 2
    assert asks_used(pid, 7, 'tv', perms=both, music_db=db, video_db=v) == 8      # show, episode, youtube
    assert asks_used(pid, 7, 'music', perms=both, music_db=db, video_db=v) == 0   # no movie or show ever
    # a profile with no TV: youtube rides with movies, never a limit it can't see
    movies_only = {'movies.access': True, 'tv.access': False}
    assert asks_used(pid, 7, 'movies', perms=movies_only, music_db=db, video_db=v) == 6
    # a logged ask whose kind isn't known counts toward both video limits
    old = _VideoCount({'movie': 1, None: 2})
    assert asks_used(pid, 7, 'movies', perms=both, video_db=old) == 3
    assert asks_used(pid, 7, 'tv', perms=both, video_db=old) == 2
    assert asks_used(pid, 7, 'movies', music_db=db, video_db=_VideoCount(None)) is None    # unreadable: full
    with pytest.raises(ValueError):
        asks_used(pid, 7, 'books', video_db=v)


def test_a_music_ask_never_spends_on_movies_or_tv(made, monkeypatch):
    import core.requests.asks as asks
    db = _db()
    pid = made('teen', **{'music.request_limit': {'n': 1, 'days': 7}})
    # plenty of movie and show asks this week: none of them is music's
    monkeypatch.setattr(asks, '_video_db', lambda db=None: db or _VideoCount({'movie': 5, 'show': 5}))
    track = {'id': 'fu%s' % uuid4().hex[:6], 'name': 'song', 'artists': [{'name': 'A'}],
             'album': {'id': 'al%s' % uuid4().hex[:6], 'name': 'Al', 'album_type': 'album', 'images': []}}
    out = db.add_to_wishlist_detailed(spotify_track_data=track, profile_id=pid, source_type='manual',
                                      user_initiated=True)
    assert out.get('created'), out
    # and the one music ask is spent now
    track2 = dict(track, id='fv%s' % uuid4().hex[:6],
                  album={'id': 'am%s' % uuid4().hex[:6], 'name': 'Bl', 'album_type': 'album', 'images': []})
    out = db.add_to_wishlist_detailed(spotify_track_data=track2, profile_id=pid, source_type='manual',
                                      user_initiated=True)
    assert out.get('status') == 'rejected' and out.get('reason') == 'request_limit', out
    db.clear_wishlist(profile_id=pid)


def test_the_admin_sees_asks_used_per_person_per_kind(made, monkeypatch):
    import api.permissions_routes as routes
    monkeypatch.setattr(routes, 'get_video_db', lambda: _VideoCount({'movie': 1, 'show': 2}))
    limited = made('teen', **{'movies.request_limit': {'n': 5, 'days': 7}, 'tv.request_limit': {'n': 3, 'days': 1}})
    free = made()
    body = _as(1).get('/api/permissions/asks-used').get_json()
    assert body['used'][str(limited)] == {
        'movies': {'kind': 'movies', 'limit': 5, 'days': 7, 'used': 1, 'remaining': 4},
        'tv': {'kind': 'tv', 'limit': 3, 'days': 1, 'used': 2, 'remaining': 1}}
    assert str(free) not in body['used']
    assert _as(limited).get('/api/permissions/asks-used').status_code == 403


def test_a_member_reads_their_own_asks_per_kind(made, monkeypatch):
    import api.permissions_routes as routes
    monkeypatch.setattr(routes, 'get_video_db', lambda: _VideoCount({'movie': 2}))
    pid = made('teen', **{'movies.request_limit': {'n': 2, 'days': 7}})
    body = _as(pid).get('/api/profiles/me/asks').get_json()
    assert body['asks'] == {'movies': {'kind': 'movies', 'limit': 2, 'days': 7, 'used': 2, 'remaining': 0}}
    assert _as(1).get('/api/profiles/me/asks').get_json()['asks'] == {}     # admins have no limit


def test_the_old_shared_limit_still_reads_as_every_kind():
    # an older caller that sets requests.limit sets all three, and can()
    # on it is held to the strictest
    layer = P.clean_layer({'requests.limit': {'n': 4, 'days': 7}, 'tv.request_limit': {'n': 1, 'days': 7}})
    assert layer == {'music.request_limit': {'n': 4, 'days': 7}, 'movies.request_limit': {'n': 4, 'days': 7},
                     'tv.request_limit': {'n': 1, 'days': 7}}
    ctx = P.make_context(9, perms=dict(P.TEMPLATE_DEFAULTS, **layer))
    assert P.can(ctx, 'requests.limit', 0) and not P.can(ctx, 'requests.limit', 1)
    assert P.can(ctx, 'music.request_limit', 3) and not P.can(ctx, 'music.request_limit', 4)


def test_the_old_columns_start_every_kind():
    perms, reasons = P.perms_from_legacy({'allowed_sides': 'both', 'can_download': False,
                                          'request_limit': 5, 'request_limit_days': 30})
    assert reasons == []
    for row in P.LIMIT_ROWS.values():
        assert perms[row] == {'n': 5, 'days': 30}
    # the columns cache the music limit
    eff = dict(perms, **{'music.request_limit': {'n': 2, 'days': 7}})
    assert P.legacy_from_perms(eff)['request_limit'] == 2
    # the migration's verify step holds movies and tv to the old column too
    old = {'allowed_sides': 'both', 'can_download': False, 'request_limit': 5, 'request_limit_days': 30}
    assert not {'music.request_limit', 'movies.request_limit', 'tv.request_limit'} & set(
        P.cache_matches(old, perms, strict=True))
    moved = P.cache_matches(old, dict(perms, **{'tv.request_limit': {'n': 1, 'days': 30}}), strict=True)
    assert 'tv.request_limit' in moved and 'movies.request_limit' not in moved
