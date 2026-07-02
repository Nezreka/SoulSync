"""Regression tests for JellyfinClient.reorder_playlist ('Align playlists').

Ashh (Docker/Jellyfin): "Failed to reorder playlist" on every mirrored playlist,
with `Jellyfin reorder move failed ...: HTTP 400`.

Root cause: reorder used Jellyfin's per-item Move endpoint
(`POST /Playlists/{id}/Items/{entryId}/Move/{i}`), which is user-scoped and — unlike
the add/remove item endpoints, which take an explicit `UserId` — can't resolve a
user from a server API key, so it 400s for everyone using an API key. The fix
reorders via the same `UserId`-scoped `POST /Items` the sync already uses
(append preserves order), then removes the original entries. Add-before-remove so
a failed step never empties the playlist.
"""

from __future__ import annotations

import requests

from core.jellyfin_client import JellyfinClient


def _client():
    c = JellyfinClient.__new__(JellyfinClient)   # skip heavy __init__
    c.base_url = "http://jf"
    c.api_key = "KEY"
    c.user_id = "USER1"
    c.ensure_connection = lambda: True
    return c


class _Resp:
    def __init__(self, status_code=204):
        self.status_code = status_code


A = "a" * 32
B = "b" * 32
C = "c" * 32


def test_reorder_uses_userid_scoped_add_in_order_then_removes_old(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post",
                        lambda url, params=None, headers=None, timeout=None: (
                            calls.append(("POST", url, params)) or _Resp(204)))
    monkeypatch.setattr(requests, "delete",
                        lambda url, params=None, headers=None, timeout=None: (
                            calls.append(("DELETE", url, params)) or _Resp(204)))

    c = _client()
    c._make_request = lambda ep, params=None: {'Items': [
        {'Id': A, 'PlaylistItemId': 'eA'},
        {'Id': B, 'PlaylistItemId': 'eB'},
        {'Id': C, 'PlaylistItemId': 'eC'},
    ]}

    ok = c.reorder_playlist('PL', 'My PL', [C, A, B])
    assert ok is True

    # The broken Move endpoint is never touched.
    assert not any('/Move/' in url for _m, url, _p in calls)

    # Exactly one add POST, carrying UserId (the fix) and the ids IN ORDER.
    posts = [(url, params) for m, url, params in calls if m == "POST"]
    assert len(posts) == 1
    url, params = posts[0]
    assert url == "http://jf/Playlists/PL/Items"
    assert params['UserId'] == 'USER1'
    assert params['Ids'] == f'{C},{A},{B}'           # append order == desired order

    # Original entries removed afterward, leaving only the re-added ones.
    deletes = [params for m, url, params in calls if m == "DELETE"]
    assert len(deletes) == 1
    assert set(deletes[0]['EntryIds'].split(',')) == {'eA', 'eB', 'eC'}

    # Ordering: every add happens before any remove (never empties the playlist).
    kinds = [m for m, _u, _p in calls]
    assert kinds.index('DELETE') > max(i for i, k in enumerate(kinds) if k == 'POST')


def test_reorder_add_failure_leaves_playlist_untouched(monkeypatch):
    """If the re-add 400s, we must NOT proceed to remove the originals — otherwise
    a failed reorder would empty the mirrored playlist."""
    calls = []
    monkeypatch.setattr(requests, "post",
                        lambda url, params=None, headers=None, timeout=None: (
                            calls.append("POST") or _Resp(400)))
    monkeypatch.setattr(requests, "delete",
                        lambda url, params=None, headers=None, timeout=None: (
                            calls.append("DELETE") or _Resp(204)))

    c = _client()
    c._make_request = lambda ep, params=None: {'Items': [
        {'Id': A, 'PlaylistItemId': 'eA'},
        {'Id': B, 'PlaylistItemId': 'eB'},
    ]}

    ok = c.reorder_playlist('PL', 'My PL', [B, A])
    assert ok is False
    assert 'DELETE' not in calls          # never removed anything → playlist intact


def test_reorder_empty_playlist_is_failure(monkeypatch):
    c = _client()
    c._make_request = lambda ep, params=None: {'Items': []}
    assert c.reorder_playlist('PL', 'My PL', [A]) is False
