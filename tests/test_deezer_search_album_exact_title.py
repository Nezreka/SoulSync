"""Deezer search_album picks the exact-title album, not results[0].

Example: the worker searched "Various Artists Brave (Original Soundtrack)" and
Deezer returned other Various Artists compilations, so the real album (id
3410931) was never matched.
"""
from unittest.mock import MagicMock

import pytest

from core.deezer_client import DeezerClient


def _album(id_, title, artist):
    return {'id': id_, 'title': title, 'artist': {'name': artist}}


BRAVE = _album(3410931, 'Brave (Original Soundtrack)', 'Various Artists')
NOISE = [_album(i, f'Hits {i}', 'Various Artists') for i in range(1, 19)]


@pytest.fixture
def client(monkeypatch):
    c = DeezerClient.__new__(DeezerClient)
    c.session = MagicMock()
    monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: MagicMock())
    c.calls = []

    def fake_get(url, params=None, timeout=None):
        c.calls.append(dict(params or {}))
        q = (params or {}).get('q', '')
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if q.startswith('album:'):
            data = NOISE + [BRAVE]
        else:
            data = NOISE  # plain query floods with other compilations
        resp.json = lambda: {'data': data}
        return resp

    c.session.get = fake_get
    return c


def _call(client, artist, title):
    return DeezerClient.search_album.__wrapped__(client, artist, title) \
        if hasattr(DeezerClient.search_album, '__wrapped__') else client.search_album(artist, title)


def test_various_artists_finds_exact_title(client):
    r = _call(client, 'Various Artists', 'Brave (Original Soundtrack)')
    assert r['id'] == 3410931
    assert client.calls[0]['q'] == 'album:"Brave (Original Soundtrack)"'
    assert client.calls[0]['limit'] == 50


def test_empty_artist_matches_on_title(client):
    assert _call(client, '', 'Brave (Original Soundtrack)')['id'] == 3410931


def test_named_artist_must_match(client):
    # exact title but a different artist: not accepted, falls back to old query
    r = _call(client, 'Someone Else', 'Brave (Original Soundtrack)')
    assert r['id'] == 1  # old behavior: results[0] of the plain query
    assert client.calls[-1]['q'] == 'Someone Else Brave (Original Soundtrack)'


def test_no_exact_title_falls_back_to_first(client):
    assert _call(client, 'Various Artists', 'Nothing Like This')['id'] == 1


def test_empty_title_uses_plain_query(client):
    _call(client, 'Some Artist', '')
    assert all(not c['q'].startswith('album:') for c in client.calls)


def test_punctuation_and_case_fold(client):
    assert _call(client, 'various artists', 'brave original soundtrack')['id'] == 3410931

def test_various_artists_takes_a_lone_exact_title_credited_to_someone(client):
    # deezer credits plenty of soundtracks to the composer, not VA
    from core.deezer_client import DeezerClient as D
    only = [_album(5, 'Brave (Original Soundtrack)', 'Patrick Doyle')]
    assert D._pick_album_by_title(client, only, 'Various Artists', 'Brave (Original Soundtrack)')['id'] == 5


def test_various_artists_generic_title_is_not_a_guess(client):
    # several same-titled albums and none by VA: any pick would be a guess
    from core.deezer_client import DeezerClient as D
    many = [_album(5, 'Greatest Hits', 'Queen'), _album(6, 'Greatest Hits', 'ABBA')]
    assert D._pick_album_by_title(client, many, 'Various Artists', 'Greatest Hits') is None

