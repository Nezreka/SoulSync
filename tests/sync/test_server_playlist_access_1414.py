"""#1414: the server playlists page showed every playlist on the server to every
profile, through the admin account, and let anyone edit or delete any of them.

a profile now reaches only its own playlists: through its own server user when
it has one (navidrome also drops other users' public playlists by owner), and on
the shared account only the ones its own mirrors made. the admin sees everything,
with everyone else's grouped by owner.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.sync import server_playlist_access as spa


def _pl(pid, title, owner=None, n=3):
    return SimpleNamespace(id=pid, title=title, owner=owner, leaf_count=n)


class _Nav:
    """a navidrome-shaped client: get_all_playlists + acting_as on a view."""

    def __init__(self, playlists, username='soulsync', acting_as=None):
        self.playlists = playlists
        self.username = username
        if acting_as:
            self.acting_as = acting_as
        self.deleted = []

    def get_all_playlists(self):
        return list(self.playlists)

    def is_connected(self):
        return True

    def delete_playlist(self, playlist_id):
        self.deleted.append(str(playlist_id))
        return True


# ── the decision ─────────────────────────────────────────────────────────────

def test_a_linked_navidrome_user_does_not_see_other_users_public_playlists():
    view = _Nav([_pl('1', 'Mine', 'thomas'), _pl('2', 'Public Party', 'guest')], acting_as='thomas')
    scope = spa.PlaylistScope(client=view, is_admin=False, acting_as='thomas')
    assert [p.title for p in spa.visible_playlists('navidrome', scope)] == ['Mine']


def test_a_shared_account_profile_sees_only_what_its_mirrors_made():
    base = _Nav([_pl('1', 'Bedtime'), _pl('2', 'Chill Mix'), _pl('3', 'Gym')])
    scope = spa.PlaylistScope(client=base, is_admin=False, own_names={'bedtime'})
    assert [p.title for p in spa.visible_playlists('navidrome', scope)] == ['Bedtime']


def test_another_users_id_paired_with_your_own_name_is_refused():
    """the endpoints act id-first; passing on the name and then editing by id
    would hand anyone a way into any playlist."""
    base = _Nav([_pl('1', 'Bedtime'), _pl('2', 'Chill Mix')])
    scope = spa.PlaylistScope(client=base, is_admin=False, own_names={'bedtime'})
    assert spa.resolve_allowed('navidrome', scope, '2', 'Bedtime') == ('1', 'Bedtime')
    assert spa.resolve_allowed('navidrome', scope, '2', 'Chill Mix') is None


def test_a_stale_id_resolves_to_the_live_playlist_by_name():
    """plex and jellyfin recreate a playlist on edit, so the page can hold a dead id."""
    view = _Nav([_pl('new-9', 'Mine')], acting_as='kid')
    scope = spa.PlaylistScope(client=view, is_admin=False, acting_as='kid')
    assert spa.resolve_allowed('jellyfin', scope, 'old-1', 'Mine') == ('new-9', 'Mine')


def test_the_admin_gets_what_it_asked_for():
    scope = spa.PlaylistScope(client=_Nav([]), is_admin=True)
    assert spa.resolve_allowed('navidrome', scope, '2', 'Anything') == ('2', 'Anything')


def test_plex_listing_reads_headers_not_every_track():
    class _Raw:
        def __init__(self, key, title, kind='audio'):
            self.ratingKey, self.title, self.playlistType, self.leafCount = key, title, kind, 7

        def items(self):
            raise AssertionError('listing loaded every track')

    client = SimpleNamespace(server=SimpleNamespace(
        playlists=lambda: [_Raw(1, 'Songs'), _Raw(2, 'Movies', 'video')]))
    rows = spa.list_playlists('plex', client)
    assert [(r.id, r.title, r.leaf_count) for r in rows] == [('1', 'Songs', 7)]


def test_admin_split_groups_navidrome_playlists_by_owner():
    class _DB:
        def get_all_profiles(self):
            return [{'id': 1, 'name': 'Boulder'}, {'id': 2, 'name': 'ThomasClan'}]

        def get_profile_navidrome_login(self, pid):
            return ('thomas', 'pw') if pid == 2 else None

    base = _Nav([_pl('1', 'Chill', 'soulsync'), _pl('2', 'Kids', 'thomas'),
                 _pl('3', 'Party', 'guest')])
    mine, groups = spa.admin_split('navidrome', base, _DB())
    assert [p.title for p in mine] == ['Chill']
    assert {(g['owner'], g['profile']): [p.title for p in g['playlists']] for g in groups} == {
        ('thomas', 'ThomasClan'): ['Kids'], ('guest', None): ['Party']}


# ── through the routes ───────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


# every test gets its own playlist name: the suite shares one database, and two
# shared-account profiles with the same mirror name sync under distinct names
OWN = f'Bedtime {uuid4().hex[:6]}'


@pytest.fixture()
def server(monkeypatch):
    global OWN
    OWN = f'Bedtime {uuid4().hex[:6]}'
    base = _Nav([_pl('10', OWN, 'soulsync'), _pl('11', 'Chill Mix', 'soulsync'),
                 _pl('12', 'Kids', 'thomas')])
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')
    monkeypatch.setattr(web_server.media_server_engine, 'client', lambda name=None: base)
    return base


@pytest.fixture()
def member(server):  # after server, which picks this test's name
    db = web_server.get_database()
    pid = db.create_profile(name=f'm_{uuid4().hex[:8]}')
    db.mirror_playlist('spotify', f'sp-{uuid4().hex[:6]}', OWN, [], profile_id=pid)
    return pid


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


def test_a_member_lists_only_their_own(server, member):
    body = _as(member).get('/api/server/playlists').get_json()
    assert body['scope'] == 'shared'
    assert [p['name'] for p in body['playlists']] == [OWN]
    assert body['others'] == []


def test_a_member_cannot_delete_someone_elses_playlist(server, member):
    c = _as(member)
    r = c.post('/api/server/playlist/11/delete', json={'playlist_name': 'Chill Mix'})
    assert r.status_code == 403
    # the swap: the victim's id with the member's own playlist name
    r = c.post('/api/server/playlist/11/delete', json={'playlist_name': OWN})
    assert server.deleted == ['10'], 'acted on the id it was handed, not the one it checked'


def test_a_member_can_delete_their_own(server, member):
    r = _as(member).post('/api/server/playlist/10/delete', json={'playlist_name': OWN})
    assert r.status_code == 200 and server.deleted == ['10']


def test_the_admin_sees_everyone_grouped_by_owner(server):
    body = _as(1).get('/api/server/playlists').get_json()
    assert body['scope'] == 'admin'
    assert [p['name'] for p in body['playlists']] == [OWN, 'Chill Mix']
    assert [(g['owner'], [p['name'] for p in g['playlists']]) for g in body['others']] == [
        ('thomas', ['Kids'])]
