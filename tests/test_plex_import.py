"""import plex users (#2319): the list of who the plex server is shared with,
which soulsync profile is (or may be) each one, the import itself (a profile
linked to the plex account, on the picked template, never twice) and the
admin-only routes. plex.tv is never called: plexapi's account and users are
stood in.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-pleximport-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'p.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.security import plex_import, plex_signin  # noqa: E402
from core.security.plex_import import PlexUser  # noqa: E402
from core.security.plex_signin import PlexAccount  # noqa: E402

MACHINE = 'machine-2319'


def _uid():
    return uuid4().hex[:8]


def _pid():
    return str(int(uuid4().int % 10**9) + 10**9)


class _Share:
    def __init__(self, machine):
        self.machineIdentifier = machine


class _User:
    """a plexapi MyPlexUser, the fields import reads"""

    def __init__(self, uid, username, *, title=None, email='', thumb='', home=False, restricted=False,
                 machines=(MACHINE,), token='srv-token'):
        self.id = int(uid)
        self.username = username
        self.title = title if title is not None else username
        self.email = email
        self.thumb = thumb
        self.home = home
        self.restricted = restricted
        self.servers = [_Share(m) for m in machines]
        self._token = token
        self.token_asked = []

    def get_token(self, machine):
        self.token_asked.append(machine)
        return self._token


class _Account:
    def __init__(self, users):
        self._users = users

    def users(self):
        return list(self._users)


def _db():
    return web_server.get_database()


def _template(slug='adult'):
    return next(int(t['id']) for t in _db().list_permission_templates() if t.get('slug') == slug)


def _template_of(pid):
    return _db().get_permission_inputs(int(pid)).get('template_id')


# -- who is listed --

def test_lists_friends_who_share_this_server_and_home_users():
    friend = _User(_pid(), 'friend')
    elsewhere = _User(_pid(), 'elsewhere', machines=('other-server',))
    home = _User(_pid(), 'kid', home=True, restricted=True, machines=())
    adult_home = _User(_pid(), 'partner', home=True, machines=(MACHINE,))
    users = plex_import.server_users(_Account([friend, elsewhere, home, adult_home]), MACHINE)
    by_name = {u.username: u for u in users}
    assert set(by_name) == {'friend', 'kid', 'partner'}
    assert by_name['kid'].managed and by_name['kid'].home
    assert not by_name['partner'].managed and by_name['partner'].home
    assert not by_name['friend'].home and not by_name['friend'].managed
    assert by_name['friend'].id == str(friend.id)


def test_no_server_id_lists_everyone_rather_than_nobody():
    users = plex_import.server_users(_Account([_User(_pid(), 'a', machines=('x',)), _User(_pid(), 'b')]), None)
    assert {u.username for u in users} == {'a', 'b'}


def test_the_same_account_twice_is_listed_once():
    uid = _pid()
    users = plex_import.server_users(_Account([_User(uid, 'dup'), _User(uid, 'dup')]), MACHINE)
    assert len(users) == 1


# -- who is already here --

def _plexuser(uid=None, username=None, **kw):
    return PlexUser(id=uid or _pid(), username=username or f'pu{_uid()}', title=kw.pop('title', ''), **kw)


def test_a_linked_account_is_matched_by_its_id_first():
    db = _db()
    name = f'Linked{_uid()}'
    pid = db.create_profile(name, can_download=False)
    u = _plexuser(username=f'other{_uid()}')
    db.set_profile_plex_account(pid, u.id)
    rows = plex_import.listing(db, [u])
    assert rows[0]['match'] == 'linked' and rows[0]['profile']['id'] == pid


def test_the_same_name_is_a_guess_and_never_an_admin_or_someone_linked_elsewhere():
    db = _db()
    base = f'Same{_uid()}'
    pid = db.create_profile(base, can_download=False)
    guess = plex_import.listing(db, [_plexuser(username=base.lower())])[0]
    assert guess['match'] == 'name' and guess['profile']['id'] == pid

    admin_name = f'Boss{_uid()}'
    db.create_profile(admin_name, is_admin=True)
    assert plex_import.listing(db, [_plexuser(username=admin_name)])[0]['profile'] is None

    taken = f'Taken{_uid()}'
    tid = db.create_profile(taken, can_download=False)
    db.set_profile_plex_account(tid, _pid())
    assert plex_import.listing(db, [_plexuser(username=taken)])[0]['profile'] is None


def test_an_unsafe_avatar_url_is_never_shown_or_fetched(monkeypatch):
    called = []
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: called.append(a) or None)
    assert plex_import.fetch_avatar('http://plex.tv/users/x/avatar') is None
    assert plex_import.fetch_avatar('https://169.254.169.254/latest') is None
    assert plex_import.fetch_avatar('https://plex.tv.evil.example/a') is None
    assert not called
    row = plex_import.listing(_db(), [_plexuser(thumb='https://evil.example/a.png')])[0]
    assert row['thumb'] == ''
    ok = plex_import.listing(_db(), [_plexuser(thumb='https://plex.tv/users/abc/avatar?c=1')])[0]
    assert ok['thumb'] == 'https://plex.tv/users/abc/avatar?c=1'


# -- importing --

def _users(*raw):
    return plex_import.server_users(_Account(list(raw)), MACHINE)


def test_import_makes_a_profile_linked_to_the_account_on_the_picked_template():
    db = _db()
    raw = _User(_pid(), f'imp{_uid()}', thumb='https://plex.tv/users/abc/avatar')
    saved = {}
    tid = _template('adult')
    res = plex_import.import_users(db, _users(raw), [{'id': str(raw.id)}], template_id=tid,
                                   machine_id=MACHINE, save_avatar=lambda p, b: saved.update({p: b}),
                                   avatar_fetcher=lambda url: b'img-bytes')
    assert [r.status for r in res] == ['created']
    pid = res[0].profile_id
    prof = db.get_profile(pid)
    assert prof['name'] == raw.username and not prof['is_admin']
    assert db.get_profile_by_plex_account(str(raw.id))['id'] == pid
    assert _template_of(pid) == tid
    assert saved == {pid: b'img-bytes'}
    # their server token is stored the way sign-in stores it
    assert raw.token_asked == [MACHINE]
    with db._get_connection() as conn:
        row = conn.execute("SELECT plex_home_user_id FROM profiles WHERE id = ?", (pid,)).fetchone()
    assert row[0] == str(raw.id)


def test_importing_again_never_makes_a_second_profile():
    db = _db()
    raw = _User(_pid(), f'again{_uid()}')
    first = plex_import.import_users(db, _users(raw), [{'id': str(raw.id)}], template_id=None)
    second = plex_import.import_users(db, _users(raw), [{'id': str(raw.id)}, {'id': str(raw.id)}],
                                      template_id=None)
    assert first[0].status == 'created'
    assert [r.status for r in second] == ['exists']
    assert second[0].profile_id == first[0].profile_id
    with db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM profiles WHERE plex_account_id = ?", (str(raw.id),)).fetchone()[0]
    assert n == 1


def test_a_taken_name_gets_a_number_not_the_other_profile():
    db = _db()
    name = f'Taken{_uid()}'
    other = db.create_profile(name, can_download=False)
    raw = _User(_pid(), name)
    res = plex_import.import_users(db, _users(raw), [{'id': str(raw.id)}], template_id=None)
    assert res[0].status == 'created' and res[0].profile_id != other
    assert db.get_profile(res[0].profile_id)['name'] == f'{name} 2'


def test_linking_a_profile_that_is_already_them():
    db = _db()
    pid = db.create_profile(f'Maya{_uid()}', can_download=False)
    raw = _User(_pid(), f'maya{_uid()}')
    res = plex_import.import_users(db, _users(raw), [{'id': str(raw.id), 'profile_id': pid}], template_id=None)
    assert res[0].status == 'linked' and res[0].profile_id == pid
    assert db.get_profile_by_plex_account(str(raw.id))['id'] == pid


def test_linking_keeps_a_profile_acting_as_another_plex_user():
    db = _db()
    pid = db.create_profile(f'Home{_uid()}', can_download=False)
    other_home = _pid()
    assert db.set_profile_plex_home_user(pid, other_home, 'someone', 'their-token')
    raw = _User(_pid(), f'h{_uid()}')
    # not offered as a guess either
    assert plex_import.listing(db, [PlexUser(id=str(raw.id), username=db.get_profile(pid)['name'],
                                             title='')])[0]['profile'] is None
    res = plex_import.import_users(db, _users(raw), [{'id': str(raw.id), 'profile_id': pid}], template_id=None,
                                   machine_id=MACHINE)
    assert res[0].status == 'linked'
    with db._get_connection() as conn:
        row = conn.execute("SELECT plex_home_user_id FROM profiles WHERE id = ?", (pid,)).fetchone()
    assert row[0] == other_home and raw.token_asked == []


def test_link_refuses_an_admin_and_a_profile_linked_elsewhere():
    db = _db()
    admin = db.create_profile(f'Adm{_uid()}', is_admin=True)
    taken = db.create_profile(f'Tk{_uid()}', can_download=False)
    db.set_profile_plex_account(taken, _pid())
    a, b = _User(_pid(), f'a{_uid()}'), _User(_pid(), f'b{_uid()}')
    res = plex_import.import_users(db, _users(a, b), [{'id': str(a.id), 'profile_id': admin},
                                                     {'id': str(b.id), 'profile_id': taken}], template_id=None)
    assert [r.status for r in res] == ['failed', 'failed']
    assert db.get_profile_by_plex_account(str(a.id)) is None
    assert db.get_profile_by_plex_account(str(b.id)) is None


def test_someone_not_on_the_server_list_is_skipped():
    res = plex_import.import_users(_db(), [], [{'id': '999'}], template_id=None)
    assert res[0].status == 'skipped'


def test_plex_sign_in_lands_on_the_imported_profile_with_new_sign_ups_off():
    db = _db()
    raw = _User(_pid(), f'later{_uid()}')
    pid = plex_import.import_users(db, _users(raw), [{'id': str(raw.id)}], template_id=None)[0].profile_id
    out = plex_signin.sign_in(db, PlexAccount(str(raw.id), raw.username, 'fresh-token'),
                              allow_create=False, default_can_download=False)
    assert out.error is None and out.profile_id == pid and not out.created


# -- the routes --

def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def plex(monkeypatch):
    import api.user_profiles as up
    users = [_User(_pid(), f'route{_uid()}'), _User(_pid(), f'route{_uid()}')]
    monkeypatch.setattr(up, '_plex_import_source', lambda: (_Account(users), MACHINE))
    monkeypatch.setattr(plex_import, 'fetch_avatar', lambda url: None)
    return users


def test_routes_are_declared_admin_only():
    from core.permissions import declared_access
    views = web_server.app.view_functions
    for rule in web_server.app.url_map.iter_rules():
        if rule.rule == '/api/profiles/plex-import':
            assert declared_access(views[rule.endpoint])[0] == 'admin'


def test_members_cannot_list_or_import(plex):
    member = _db().create_profile(f'mem{_uid()}', can_download=False)
    c = _as(member)
    assert c.get('/api/profiles/plex-import').status_code == 403
    assert c.post('/api/profiles/plex-import', json={'users': [{'id': str(plex[0].id)}]}).status_code == 403
    assert _db().get_profile_by_plex_account(str(plex[0].id)) is None


def test_admin_lists_and_imports_through_the_routes(plex):
    c = _as(1)
    body = c.get('/api/profiles/plex-import').get_json()
    assert body['success'] and {u['id'] for u in body['users']} >= {str(u.id) for u in plex}
    assert 'signin_enabled' in body
    tid = _template('teen')
    r = c.post('/api/profiles/plex-import', json={'users': [{'id': str(plex[0].id)}], 'template_id': tid})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()['results']
    assert res[0]['status'] == 'created'
    assert _template_of(res[0]['profile_id']) == tid
    again = c.get('/api/profiles/plex-import').get_json()['users']
    row = next(u for u in again if u['id'] == str(plex[0].id))
    assert row['match'] == 'linked' and row['profile']['id'] == res[0]['profile_id']


def test_the_route_refuses_a_bad_body_or_a_missing_template(plex):
    c = _as(1)
    assert c.post('/api/profiles/plex-import', json={}).status_code == 400
    assert c.post('/api/profiles/plex-import', json={'users': ['x']}).status_code == 400
    assert c.post('/api/profiles/plex-import',
                  json={'users': [{'id': str(plex[1].id)}], 'template_id': 987654}).status_code == 404
    assert _db().get_profile_by_plex_account(str(plex[1].id)) is None


def test_plex_down_is_said_in_words(monkeypatch):
    import api.user_profiles as up

    def boom():
        raise RuntimeError('Plex is not connected')
    monkeypatch.setattr(up, '_plex_import_source', boom)
    r = _as(1).get('/api/profiles/plex-import')
    assert r.status_code == 503 and r.get_json()['message'] == 'Plex is not connected'


# -- seerr clients see them as plex users --

def test_a_plex_linked_profile_is_a_plex_user_to_seerr():
    from core.seerr_compat import shapes
    u = shapes.user(5, 'Maya', permissions=0, plex_name='maya_plex')
    assert u['userType'] == shapes.USER_TYPE_PLEX and u['plexUsername'] == 'maya_plex'
    jf = shapes.user(5, 'Maya', permissions=0, plex_name='maya_plex', jellyfin_id='j1')
    assert jf['userType'] == shapes.USER_TYPE_JELLYFIN
    assert shapes.user(5, 'Maya', permissions=0)['plexUsername'] is None
