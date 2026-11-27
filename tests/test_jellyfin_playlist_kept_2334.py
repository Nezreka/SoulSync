"""#2334: synced playlists on jellyfin went to 0 tracks.

a replace cleared the playlist first and added the tracks after. jellyfin
answers 204 to an add of an id it doesn't know and drops it quietly (#2235),
so when soulsync's ids for the tracks had gone stale (jellyfin rescanned or
the files moved) every add was dropped and the playlist was left empty, and
the sync called it a success. a refused add went further: update_playlist
deleted the playlist and made it again, and a create whose add was refused
too left an empty one.

now the tracks go in first, the playlist is read back, and the old ones only
come out when most of what was wanted landed. otherwise it is kept as it was
and the sync says why.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from core.jellyfin_client import JellyfinClient
from services.sync_service import PlaylistSyncService
from tests.test_server_playlist_link_2098 import FakeJellyfin, _ids, _Resp, _tracks

A, B, C, D, E = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32)
STALE1, STALE2, STALE3 = ("1" * 32, "2" * 32, "3" * 32)


class StaleJellyfin(FakeJellyfin):
    """jellyfin as it is: an add of an id it doesn't know is dropped with a 204"""

    def __init__(self, playlists, known):
        super().__init__(playlists)
        self.known = set(known)

    def post(self, url, params=None, headers=None, timeout=None, json=None):
        if params and 'Ids' in params:
            kept = [i for i in params['Ids'].split(',') if i in self.known]
            if not kept:
                self.calls.append(('POST', url, params))
                return _Resp(204)
            params = dict(params, Ids=','.join(kept))
        return super().post(url, params=params, headers=headers, timeout=timeout, json=json)


@pytest.fixture
def jf(monkeypatch):
    def build(playlists, known=(A, B, C, D, E)):
        server = StaleJellyfin(playlists, known)
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


def _full():
    return {'PL1': {'name': 'daily mix 1', 'entries': [(A, 'e1'), (B, 'e2'), (C, 'e3')]}}


# ── replace ──────────────────────────────────────────────────────────────────

def test_stale_ids_never_empty_the_playlist_on_replace(jf):
    client, server = jf(_full())
    assert client.update_playlist('daily mix 1', _tracks(STALE1, STALE2, STALE3)) is False
    assert _ids(server, 'PL1') == [A, B, C]                  # it used to end at []
    assert list(server.playlists) == ['PL1']                 # not deleted and made again
    assert 'jellyfin doesn' in client.last_playlist_write_refusal


def test_mostly_stale_ids_keep_the_playlist_and_take_the_adds_back_out(jf):
    client, server = jf(_full())
    assert client.update_playlist('daily mix 1', _tracks(D, STALE1, STALE2)) is False
    assert _ids(server, 'PL1') == [A, B, C]                  # D went in and came back out


def test_a_few_stale_ids_still_replace(jf):
    client, server = jf(_full())
    assert client.update_playlist('daily mix 1', _tracks(D, E, STALE1)) is True
    assert _ids(server, 'PL1') == [D, E]                     # the readback reports the stale one
    assert client.last_playlist_write_refusal is None


def test_a_refused_add_keeps_the_playlist_instead_of_recreating_it(jf):
    client, server = jf(_full())
    server.refuse_add = True
    assert client.update_playlist('daily mix 1', _tracks(D, E)) is False
    assert list(server.playlists) == ['PL1']
    assert _ids(server, 'PL1') == [A, B, C]
    assert 'HTTP 500' in client.last_playlist_write_refusal


def test_no_jellyfin_ids_at_all_never_deletes_the_playlist(jf):
    client, server = jf(_full())
    assert client.update_playlist('daily mix 1', _tracks('not-a-guid', None)) is False
    assert _ids(server, 'PL1') == [A, B, C]


def test_an_empty_playlist_takes_what_lands(jf):
    client, server = jf({'PL1': {'name': 'daily mix 1', 'entries': []}})
    assert client.update_playlist('daily mix 1', _tracks(STALE1, D)) is True
    assert _ids(server, 'PL1') == [D]


def test_a_shrinking_source_still_shrinks_the_playlist(jf):
    # fewer tracks on purpose is not a refusal: everything wanted landed
    client, server = jf(_full())
    assert client.update_playlist('daily mix 1', _tracks(A)) is True
    assert _ids(server, 'PL1') == [A]


# ── reconcile ────────────────────────────────────────────────────────────────

def test_stale_ids_never_empty_the_playlist_on_reconcile(jf):
    client, server = jf(_full())
    assert client.reconcile_playlist('daily mix 1', _tracks(STALE1, STALE2, STALE3)) is False
    assert _ids(server, 'PL1') == [A, B, C]


def test_reconcile_with_no_ids_keeps_the_playlist(jf):
    client, server = jf(_full())
    assert client.reconcile_playlist('daily mix 1', _tracks('nope')) is False
    assert _ids(server, 'PL1') == [A, B, C]


def test_reconcile_still_adds_and_removes(jf):
    client, server = jf(_full())
    assert client.reconcile_playlist('daily mix 1', _tracks(A, D)) is True
    assert _ids(server, 'PL1') == [A, D]


# ── the sync ─────────────────────────────────────────────────────────────────

def _lib(rk, title):
    # the shape the sync hands jellyfin: a library row with both ids
    return SimpleNamespace(ratingKey=rk, id=rk, title=title, artist="Muse", album="Album")


def _src(sid, name):
    return SimpleNamespace(id=sid, name=name, artists=["Muse"], album="Album", duration_ms=200000)


def _sync(client, matches, mode):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    service._find_track_in_media_server = AsyncMock(side_effect=matches)
    client.is_connected = lambda: True
    service._get_active_media_client = lambda *a, **k: (client, 'jellyfin')
    playlist = SimpleNamespace(id="pl", name="daily mix 1",
                               tracks=[_src(str(i), f"t{i}") for i in range(len(matches))])
    return asyncio.run(service.sync_playlist(playlist, sync_mode=mode))


@pytest.mark.parametrize("mode", ["replace", "reconcile"])
def test_a_sync_with_stale_ids_keeps_the_playlist_and_says_why(jf, mode):
    client, server = jf(_full())
    matches = [(_lib(STALE1, "map of the problematique"), 1.0), (_lib(STALE2, "the man"), 1.0)]
    result = _sync(client, matches, mode)
    assert _ids(server, 'PL1') == [A, B, C]
    assert not any(m == 'DELETE' and url.endswith('/Items/PL1') for m, url, _p in server.calls)
    assert any('was not changed' in e and 'library scan' in e for e in result.errors)


def test_the_refusal_is_not_followed_by_a_replace():
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    client = Mock(spec=['reconcile_playlist', 'update_playlist', 'last_playlist_write_refusal'])
    client.reconcile_playlist.return_value = False
    client.last_playlist_write_refusal = "kept"
    assert service._reconcile_or_replace(client, 'x', []) is False
    client.update_playlist.assert_not_called()
