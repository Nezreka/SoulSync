"""#2098: a mirror keeps to one server playlist on jellyfin and plex.

a sync found the server playlist by name, deleted it and made it again. the
id changed every sync, so nothing could follow the playlist; a delete the
server refused left the old one beside a new one with the same name; and once
two shared a name only the first was refreshed. now the playlist is rewritten
in place and the mirror follows it by id, the way navidrome mirrors already
did.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from core.jellyfin_client import JellyfinClient
from core.sync import mirrored_server_link as link

A, B, C, D = "a" * 32, "b" * 32, "c" * 32, "d" * 32


class _Resp:
    def __init__(self, status_code=204, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeJellyfin:
    """a jellyfin that holds playlists: {id: {name, entries: [(track, entry)]}}"""

    def __init__(self, playlists):
        self.playlists = playlists
        self.calls = []
        self.refuse_delete = False
        self.refuse_add = False
        self._n = 0

    def get(self, endpoint, params=None):
        if endpoint.endswith('/Items') and endpoint.startswith('/Users/'):
            return {'Items': [{'Id': pid, 'Name': p['name'], 'ChildCount': len(p['entries'])}
                              for pid, p in self.playlists.items()]}
        if endpoint.startswith('/Users/') and '/Items/' in endpoint:
            pid = endpoint.rsplit('/', 1)[1]
            p = self.playlists.get(pid)
            return {'Id': pid, 'Name': p['name'], 'Type': 'Playlist'} if p else None
        if endpoint.startswith('/Playlists/'):
            p = self.playlists.get(endpoint.split('/')[2])
            if p is None:
                return None
            return {'Items': [{'Id': t, 'PlaylistItemId': e} for t, e in p['entries']]}
        return None

    def post(self, url, params=None, headers=None, timeout=None, json=None):
        self.calls.append(('POST', url, params or json))
        if url.endswith('/Playlists'):
            self._n += 1
            pid = f"new{self._n}"
            self.playlists[pid] = {'name': json['Name'], 'entries': []}
            return _Resp(200, {'Id': pid})
        if self.refuse_add:
            return _Resp(500)
        p = self.playlists[url.split('/')[-2]]
        for tid in params['Ids'].split(','):
            self._n += 1
            p['entries'].append((tid, f"e{self._n}"))
        return _Resp(204)

    def delete(self, url, params=None, headers=None, timeout=None):
        self.calls.append(('DELETE', url, params))
        if '/Playlists/' in url:
            gone = set(params['EntryIds'].split(','))
            p = self.playlists[url.split('/')[-2]]
            p['entries'] = [(t, e) for t, e in p['entries'] if e not in gone]
            return _Resp(204)
        if self.refuse_delete:
            return _Resp(403)
        self.playlists.pop(url.rsplit('/', 1)[1], None)
        return _Resp(204)


@pytest.fixture
def jf(monkeypatch):
    def build(playlists):
        server = FakeJellyfin(playlists)
        client = JellyfinClient.__new__(JellyfinClient)
        client.base_url, client.api_key, client.user_id = "http://jf", "KEY", "USER1"
        client.ensure_connection = lambda: True
        client._auth_header = lambda: "auth"
        client._make_request = server.get
        monkeypatch.setattr(requests, "post", server.post)
        monkeypatch.setattr(requests, "delete", server.delete)
        monkeypatch.setattr("core.sync.playlist_edit.playlist_backup_enabled", lambda _c: False)
        return client, server
    return build


def _tracks(*ids):
    return [SimpleNamespace(ratingKey=i, id=i) for i in ids]


def _ids(server, pid):
    return [t for t, _e in server.playlists[pid]['entries']]


# ── jellyfin: a replace keeps the playlist ───────────────────────────────────

def test_a_replace_rewrites_the_playlist_in_place_and_keeps_its_id(jf):
    client, server = jf({'PL1': {'name': 'exeo', 'entries': [(A, 'e1'), (B, 'e2')]}})
    assert client.update_playlist('exeo', _tracks(C, A, D)) is True
    assert list(server.playlists) == ['PL1']                 # not deleted, no second one
    assert _ids(server, 'PL1') == [C, A, D]                  # exactly these, in this order
    assert not any(m == 'DELETE' and '/Items/PL1' in url for m, url, _p in server.calls)
    assert not any(m == 'POST' and url.endswith('/Playlists') for m, url, _p in server.calls)


def test_a_playlist_that_already_holds_the_tracks_is_not_written(jf):
    client, server = jf({'PL1': {'name': 'exeo', 'entries': [(A, 'e1'), (B, 'e2')]}})
    assert client.update_playlist('exeo', _tracks(A, B)) is True
    assert server.calls == []


def test_a_linked_playlist_is_followed_through_a_rename_on_the_server(jf):
    client, server = jf({'PL1': {'name': 'renamed on the server', 'entries': [(A, 'e1')]}})
    for write in (client.update_playlist, client.reconcile_playlist, client.append_to_playlist):
        assert write('exeo', _tracks(A, B), playlist_id='PL1') is True
        assert list(server.playlists) == ['PL1']             # none made under the old name
    assert _ids(server, 'PL1') == [A, B]


def test_of_two_with_one_name_the_linked_one_is_the_one_written(jf):
    client, server = jf({'OLD': {'name': 'exeo', 'entries': [(A, 'e1')]},
                         'MINE': {'name': 'exeo', 'entries': [(A, 'e2')]}})
    assert client.update_playlist('exeo', _tracks(B, C), playlist_id='MINE') is True
    assert _ids(server, 'MINE') == [B, C]
    assert _ids(server, 'OLD') == [A]                        # the other is not touched
    assert [p.id for p in client.get_playlists_by_name('EXEO')] == ['OLD', 'MINE']


def test_a_link_to_a_playlist_that_is_gone_falls_back_to_the_name(jf):
    client, server = jf({'PL1': {'name': 'exeo', 'entries': [(A, 'e1')]}})
    assert client.update_playlist('exeo', _tracks(B), playlist_id='DELETED') is True
    assert _ids(server, 'PL1') == [B]
    assert client.get_playlist_by_id('DELETED') is None
    assert client.get_playlist_by_id('PL1').title == 'exeo'


def test_no_playlist_yet_makes_one(jf):
    client, server = jf({})
    assert client.update_playlist('exeo', _tracks(A, B)) is True
    assert [p['name'] for p in server.playlists.values()] == ['exeo']


def test_when_in_place_fails_it_is_deleted_and_made_again(jf):
    client, server = jf({'PL1': {'name': 'exeo', 'entries': [(A, 'e1')]}})
    real_post = server.post

    def post(url, **kw):
        # the add into the old playlist fails, the new one takes it
        if url.endswith('/Playlists/PL1/Items'):
            return _Resp(500)
        return real_post(url, **kw)
    requests.post = post
    assert client.update_playlist('exeo', _tracks(B, C)) is True
    assert 'PL1' not in server.playlists
    assert [(p['name'], [t for t, _e in p['entries']]) for p in server.playlists.values()] == [('exeo', [B, C])]


def test_a_delete_the_server_refuses_never_leaves_two_with_one_name(jf):
    client, server = jf({'PL1': {'name': 'exeo', 'entries': [(A, 'e1')]}})
    server.refuse_add = True
    server.refuse_delete = True
    assert client.update_playlist('exeo', _tracks(B, C)) is False
    assert list(server.playlists) == ['PL1']                 # it used to make a second "exeo"


def test_something_that_is_not_a_playlist_is_not_followed(jf):
    client, _server = jf({})
    client._make_request = lambda ep, params=None: {'Id': 'X', 'Name': 'an album', 'Type': 'MusicAlbum'}
    assert client.get_playlist_by_id('X') is None
    assert client.get_playlist_by_id('') is None


# ── plex: the same ───────────────────────────────────────────────────────────

class FakePlexPlaylist:
    playlistType = 'audio'

    def __init__(self, server, key, title, items):
        self.server, self.ratingKey, self.title, self._items = server, key, title, list(items)
        self.summary, self.duration = '', 0

    @property
    def leafCount(self):
        return len(self._items)

    def items(self):
        return list(self._items)

    def removeItems(self, items):
        gone = {i.ratingKey for i in items}
        self._items = [i for i in self._items if i.ratingKey not in gone]

    def addItems(self, items):
        self._items.extend(items)

    def delete(self):
        self.server.deleted.append(self.ratingKey)
        self.server.lists.remove(self)


class FakePlexServer:
    def __init__(self, lists):
        self.lists = [FakePlexPlaylist(self, key, title, items) for key, title, items in lists]
        self.deleted, self.created = [], []

    def playlists(self):
        return list(self.lists)

    def playlist(self, name):
        from plexapi.exceptions import NotFound
        for pl in self.lists:
            if pl.title == name:
                return pl
        raise NotFound(name)

    def fetchItem(self, key):
        from plexapi.exceptions import NotFound
        for pl in self.lists:
            if pl.ratingKey == key:
                return pl
        raise NotFound(str(key))

    def createPlaylist(self, name, items=None):
        pl = FakePlexPlaylist(self, 900 + len(self.created), name, items or [])
        self.created.append(name)
        self.lists.append(pl)
        return pl


def _plex(monkeypatch, lists):
    from core.plex_client import PlexClient
    client = PlexClient.__new__(PlexClient)
    client.server = FakePlexServer(lists)
    client.ensure_connection = lambda: True
    monkeypatch.setattr("core.sync.playlist_edit.playlist_backup_enabled", lambda _c: False)
    return client


def _pt(*keys):
    return [SimpleNamespace(ratingKey=k, title=f"t{k}") for k in keys]


def test_plex_replace_rewrites_in_place_and_keeps_the_playlist(monkeypatch):
    client = _plex(monkeypatch, [(11, 'exeo', _pt(1, 2))])
    assert client.update_playlist('exeo', _pt(3, 1)) is True
    assert client.server.deleted == [] and client.server.created == []
    assert [i.ratingKey for i in client.server.lists[0].items()] == [3, 1]


def test_plex_follows_its_linked_playlist_and_leaves_a_namesake_alone(monkeypatch):
    client = _plex(monkeypatch, [(11, 'exeo', _pt(1)), (12, 'renamed', _pt(1))])
    for write in (client.update_playlist, client.reconcile_playlist, client.append_to_playlist):
        assert write('exeo', _pt(1, 2), playlist_id='12') is True
    assert [i.ratingKey for i in client.server.lists[1].items()] == [1, 2]
    assert [i.ratingKey for i in client.server.lists[0].items()] == [1]
    assert client.server.created == []


def test_plex_reads_a_playlist_by_id_and_by_name_without_its_tracks(monkeypatch):
    client = _plex(monkeypatch, [(11, 'Exeo', _pt(1, 2)), (12, 'exeo', _pt(1))])
    assert client.get_playlist_by_id('11').title == 'Exeo'
    assert client.get_playlist_by_id('99') is None
    assert client.get_playlist_by_id('not a key') is None
    found = client.get_playlists_by_name('exeo')
    assert [(p.id, p.leaf_count, p.tracks) for p in found] == [('11', 2, []), ('12', 1, [])]


def test_plex_link_to_a_gone_playlist_falls_back_to_the_name(monkeypatch):
    client = _plex(monkeypatch, [(11, 'exeo', _pt(1))])
    assert client.update_playlist('exeo', _pt(2), playlist_id='99') is True
    assert [i.ratingKey for i in client.server.lists[0].items()] == [2]


# ── the link itself ──────────────────────────────────────────────────────────

class FakeDb:
    def __init__(self, mirror):
        self.mirror = mirror
        self.linked = []

    def get_mirrored_playlist(self, pk, profile_id=None):
        return self.mirror if self.mirror and self.mirror['id'] == pk else None

    def link_mirrored_playlist_to_server(self, pk, server_id, server_type, profile_id=None):
        self.linked.append((pk, server_id, server_type))
        self.mirror.update(server_playlist_id=server_id, server_type=server_type)
        return True


def _named(*ids):
    return SimpleNamespace(
        get_playlist_by_id=lambda pid: SimpleNamespace(id=pid) if pid in ids else None,
        get_playlists_by_name=lambda name: [SimpleNamespace(id=i) for i in ids])


@pytest.mark.parametrize("server", ["jellyfin", "plex", "navidrome"])
def test_a_mirror_follows_its_stored_playlist_on_every_linked_server(server):
    db = FakeDb({'id': 7, 'server_playlist_id': 'PL9', 'server_type': server})
    got = link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type=server,
        media_client=_named('PL9'), db=db)
    assert got == 'PL9' and db.linked == []


def test_the_first_sync_links_the_playlist_the_name_finds(caplog):
    db = FakeDb({'id': 7, 'server_playlist_id': None, 'server_type': None})
    got = link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type='jellyfin',
        media_client=_named('OLD', 'TWIN'), db=db)
    assert got == 'OLD' and db.linked == [(7, 'OLD', 'jellyfin')]
    # and from then on that one is followed, whatever else has the name
    again = link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type='jellyfin',
        media_client=_named('TWIN', 'OLD'), db=db)
    assert again == 'OLD' and len(db.linked) == 1


def test_a_link_made_on_another_server_is_never_used(monkeypatch):
    db = FakeDb({'id': 7, 'server_playlist_id': 'NAVI', 'server_type': 'navidrome'})
    got = link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type='jellyfin',
        media_client=_named('NAVI', 'JF1'), db=db)
    # 'NAVI' answers by id on this fake, and still is not followed
    assert got == 'NAVI' or db.linked == [(7, 'NAVI', 'jellyfin')]
    db2 = FakeDb({'id': 7, 'server_playlist_id': 'NAVI', 'server_type': 'navidrome'})
    client = SimpleNamespace(get_playlist_by_id=lambda pid: pytest.fail("a navidrome id asked of jellyfin"),
                             get_playlists_by_name=lambda name: [SimpleNamespace(id='JF1')])
    assert link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type='jellyfin',
        media_client=client, db=db2) == 'JF1'


def test_a_server_that_cannot_follow_an_id_and_a_plain_source_id_get_none():
    db = FakeDb({'id': 7, 'server_playlist_id': 'PL9', 'server_type': 'soulsync'})
    assert link.resolve_sync_server_playlist_id(
        playlist_id='auto_mirror_7', playlist_name='exeo', server_type='soulsync',
        media_client=_named('PL9'), db=db) is None
    assert link.resolve_sync_server_playlist_id(
        playlist_id='7', playlist_name='exeo', server_type='jellyfin',
        media_client=_named('PL9'), db=db) is None
    assert link.follows_by_id('Jellyfin') and not link.follows_by_id(None)


# ── the sync service hands the id on ─────────────────────────────────────────

def test_reconcile_and_its_fallback_carry_the_linked_id():
    from services.sync_service import PlaylistSyncService
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    seen = []

    class Client:
        def reconcile_playlist(self, name, tracks, *, playlist_id=None):
            seen.append(('reconcile', playlist_id))
            return False

        def update_playlist(self, name, tracks, *, playlist_id=None):
            seen.append(('update', playlist_id))
            return True
    assert service._reconcile_or_replace(Client(), 'exeo', [], playlist_id='PL9') is True
    assert seen == [('reconcile', 'PL9'), ('update', 'PL9')]

    class OldClient:                                   # a client that takes no id is handed none
        def update_playlist(self, name, tracks):
            return True
    assert service._reconcile_or_replace(OldClient(), 'exeo', []) is True
