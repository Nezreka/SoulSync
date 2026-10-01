"""Deezer genres for the discovery pool (boulder, sept 30).

deezer's artist object has no genres, only albums carry a genre_id, so
DeezerClient.get_artist() always answered 'genres': [] and the discovery scan
stored every deezer track with no genre. 166k deezer tracks on boulder's pool,
zero genres, so Daily Mix 1 ("Pop") and every genre playlist came back empty
and Run now looked like it did nothing.
"""

import json
import sqlite3
from contextlib import contextmanager

import pytest

from core.discovery.deezer_genre_backfill import backfill_deezer_discovery_genres
from core.metadata.deezer_genres import (
    artist_genres_from_albums,
    genre_names_from_response,
    rank_album_genre_ids,
)

GENRES = {132: 'Pop', 113: 'Dance', 106: 'Electro', 116: 'Rap/Hip Hop'}


# ── pure helpers ─────────────────────────────────────────────────────────


def test_genre_ids_rank_by_how_many_albums_use_them():
    albums = [
        {'genre_id': 113}, {'genre_id': 132}, {'genre_id': 132},
        {'genre_id': -1}, {'genre_id': 0}, {'genre_id': 'junk'}, {}, 'not a dict',
    ]
    assert rank_album_genre_ids(albums) == [132, 113]


def test_ties_go_to_the_newest_album():
    # deezer lists newest first
    assert rank_album_genre_ids([{'genre_id': 106}, {'genre_id': 132}]) == [106, 132]


def test_artist_genres_are_names_capped_and_unknown_ids_dropped():
    albums = [{'genre_id': g} for g in (132, 132, 113, 106, 116, 999)]
    assert artist_genres_from_albums(albums, GENRES) == ['Pop', 'Dance', 'Electro']
    assert artist_genres_from_albums([], GENRES) == []


def test_genre_map_drops_the_all_entry():
    data = {'data': [{'id': 0, 'name': 'All'}, {'id': 132, 'name': 'Pop'}, {'id': 'x'}]}
    assert genre_names_from_response(data) == {132: 'Pop'}
    assert genre_names_from_response(None) == {}


# ── DeezerClient.get_artist_genres ───────────────────────────────────────


@pytest.fixture
def deezer(monkeypatch):
    import core.deezer_client as dc
    import core.deezer_throttle as throttle

    monkeypatch.setattr(throttle, 'wait_for_slot', lambda *a, **k: None)
    client = dc.DeezerClient.__new__(dc.DeezerClient)
    calls = []

    def api_get(endpoint, params=None, **kwargs):
        calls.append(endpoint)
        if endpoint == 'genre':
            return {'data': [{'id': k, 'name': v} for k, v in GENRES.items()]}
        if endpoint.endswith('/albums'):
            return {'data': [{'genre_id': 116}, {'genre_id': 116}, {'genre_id': 132}]}
        return None

    client._api_get = api_get
    cache_hits = {}
    monkeypatch.setattr(dc, 'get_metadata_cache', lambda: object())
    monkeypatch.setattr(
        dc,
        'get_cached_artist_album_items',
        lambda cache, source, artist_id, album_type, limit: cache_hits.get(
            (artist_id, album_type, limit)
        ),
    )
    return client, calls, cache_hits


def test_uses_the_album_list_the_scan_just_cached(deezer):
    client, calls, cache_hits = deezer
    cache_hits[('55', 'album,single,ep', 50)] = [{'genre_id': 106}, {'genre_id': 106}]
    assert client.get_artist_genres('55') == ['Electro']
    # no album call, the scan already paid for it. only the one-off genre map.
    assert calls == ['genre']
    client.get_artist_genres('55')
    assert calls == ['genre']


def test_fetches_the_album_list_when_nothing_is_cached(deezer):
    client, calls, _ = deezer
    assert client.get_artist_genres('77') == ['Rap/Hip Hop', 'Pop']
    assert calls == ['artist/77/albums', 'genre']


def test_never_raises(deezer):
    client, _, _ = deezer

    def boom(*a, **k):
        raise RuntimeError('deezer down')

    client._api_get = boom
    assert client.get_artist_genres('77') == []
    assert client.get_artist_genres('') == []


# ── the scanner hook ─────────────────────────────────────────────────────


def test_scanner_fills_deezer_genres_on_artist_lookup(monkeypatch):
    import core.watchlist_scanner as ws

    class Client:
        def get_artist(self, artist_id):
            return {'id': artist_id, 'genres': []}

        def get_artist_genres(self, artist_id):
            return ['Pop']

    monkeypatch.setattr(ws, 'get_client_for_source', lambda source: Client())
    scanner = ws.WatchlistScanner.__new__(ws.WatchlistScanner)
    assert scanner._get_artist_data_for_source('deezer', '9')['genres'] == ['Pop']
    # other sources untouched
    assert scanner._get_artist_data_for_source('itunes', '9')['genres'] == []


# ── the backfill ─────────────────────────────────────────────────────────


class _Db:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def _get_connection(self):
        yield self.conn


def _pool(rows):
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE discovery_pool (id INTEGER PRIMARY KEY, source TEXT, '
        'deezer_artist_id TEXT, artist_genres TEXT)'
    )
    conn.executemany(
        'INSERT INTO discovery_pool (source, deezer_artist_id, artist_genres) VALUES (?, ?, ?)',
        rows,
    )
    return _Db(conn)


def _genres_by_artist(db):
    return dict(
        db.conn.execute(
            'SELECT deezer_artist_id, artist_genres FROM discovery_pool '
            "WHERE source = 'deezer' GROUP BY deezer_artist_id"
        ).fetchall()
    )


def test_backfill_fills_every_track_of_each_artist():
    db = _pool([
        ('deezer', '1', None), ('deezer', '1', None), ('deezer', '2', None),
        ('spotify', '1', None),  # another source's rows are never touched
    ])
    out = backfill_deezer_discovery_genres(db, {'1': ['Pop'], '2': []}.get)
    assert out == {'artists': 2, 'with_genres': 1, 'rows': 3}
    assert _genres_by_artist(db) == {'1': json.dumps(['Pop']), '2': '[]'}
    assert db.conn.execute(
        "SELECT artist_genres FROM discovery_pool WHERE source = 'spotify'"
    ).fetchone() == (None,)


def test_checked_artists_are_not_asked_again_and_failures_are():
    db = _pool([('deezer', '1', '[]'), ('deezer', '2', None), ('deezer', '3', None)])
    asked = []

    def lookup(artist_id):
        asked.append(artist_id)
        if artist_id == '3':
            raise RuntimeError('timeout')
        return ['Dance']

    backfill_deezer_discovery_genres(db, lookup)
    assert sorted(asked) == ['2', '3']  # '1' was already checked
    genres = _genres_by_artist(db)
    assert genres['2'] == json.dumps(['Dance'])
    assert genres['3'] is None  # a failed lookup stays NULL, retried next scan


def test_backfill_takes_a_batch_not_the_whole_pool():
    db = _pool([('deezer', str(i), None) for i in range(10)])
    out = backfill_deezer_discovery_genres(db, lambda a: ['Pop'], max_artists=4)
    assert out['artists'] == 4
