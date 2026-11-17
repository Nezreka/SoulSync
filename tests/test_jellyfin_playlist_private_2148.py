"""#2148: playlists SoulSync made on Jellyfin were always public.

jellyfin 10.9+ CreatePlaylistDto.IsPublic defaults to true, and the create
body never set it, so a playlist made for one person showed up for every user
on the server. now it is private unless the playlist being remade was public.
"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from core.jellyfin_client import JellyfinClient, playlist_create_body

A = "a" * 32
B = "b" * 32


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ''

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _client(playlist=None):
    c = JellyfinClient.__new__(JellyfinClient)   # skip heavy __init__
    c.base_url = "http://jf"
    c.api_key = "KEY"
    c.user_id = "USER1"
    c.ensure_connection = lambda: True
    c._auth_header = lambda: 'MediaBrowser Token="KEY"'
    c._make_request = lambda ep, params=None: playlist
    return c


def _record(monkeypatch):
    created = []

    def post(url, json=None, params=None, headers=None, timeout=None):
        if url == "http://jf/Playlists":
            created.append(json)
            return _Resp(200, {'Id': 'NEW'})
        return _Resp(204)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "delete", lambda *a, **k: _Resp(204))
    return created


def test_create_body_is_private_and_has_no_ids():
    body = playlist_create_body('Mix', 'USER1')
    assert body == {'Name': 'Mix', 'UserId': 'USER1', 'MediaType': 'Audio', 'IsPublic': False}


def test_a_new_playlist_is_made_private(monkeypatch):
    created = _record(monkeypatch)
    tracks = [SimpleNamespace(id=A), SimpleNamespace(id=B)]
    assert _client().create_playlist('Mix', tracks) is True
    assert created == [{'Name': 'Mix', 'UserId': 'USER1', 'MediaType': 'Audio', 'IsPublic': False}]


def test_a_remade_playlist_keeps_the_visibility_it_had(monkeypatch):
    # in place failed, so it is deleted and made again: a playlist the user
    # made public on the server stays public
    created = _record(monkeypatch)
    c = _client(playlist={'OpenAccess': True})
    c._target_playlist = lambda name, pid: SimpleNamespace(id='OLD', title=name)
    c._replace_in_place = lambda *a, **k: False
    monkeypatch.setattr('core.sync.playlist_edit.playlist_backup_enabled', lambda cm: False)
    assert c.update_playlist('Mix', [SimpleNamespace(id=A)]) is True
    assert created[0]['IsPublic'] is True


def test_a_remade_playlist_on_a_server_that_does_not_say_stays_private(monkeypatch):
    created = _record(monkeypatch)
    c = _client(playlist={})   # emby / older jellyfin: no OpenAccess
    c._target_playlist = lambda name, pid: SimpleNamespace(id='OLD', title=name)
    c._replace_in_place = lambda *a, **k: False
    monkeypatch.setattr('core.sync.playlist_edit.playlist_backup_enabled', lambda cm: False)
    assert c.update_playlist('Mix', [SimpleNamespace(id=A)]) is True
    assert created[0]['IsPublic'] is False
