"""Tests for core/search/sources.py — per-source-kind + multi-kind executor."""

from __future__ import annotations

from core.search import sources


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Artist:
    def __init__(self, id_, name, image_url=None, external_urls=None):
        self.id = id_
        self.name = name
        self.image_url = image_url
        self.external_urls = external_urls


class _Album:
    def __init__(self, id_, name, artists=None, image_url=None, release_date=None,
                 total_tracks=10, album_type='album', external_urls=None):
        self.id = id_
        self.name = name
        self.artists = artists or []
        self.image_url = image_url
        self.release_date = release_date
        self.total_tracks = total_tracks
        self.album_type = album_type
        self.external_urls = external_urls


class _Track:
    def __init__(self, id_, name, artists=None, album=None, duration_ms=180000,
                 image_url=None, release_date=None, external_urls=None):
        self.id = id_
        self.name = name
        self.artists = artists or []
        self.album = album
        self.duration_ms = duration_ms
        self.image_url = image_url
        self.release_date = release_date
        self.external_urls = external_urls


class _Client:
    def __init__(self, artists=None, albums=None, tracks=None, fail=None):
        self._artists = artists or []
        self._albums = albums or []
        self._tracks = tracks or []
        self._fail = fail or set()

    def search_artists(self, q, limit=10):
        if 'artists' in self._fail:
            raise RuntimeError("artists boom")
        return self._artists

    def search_albums(self, q, limit=10):
        if 'albums' in self._fail:
            raise RuntimeError("albums boom")
        return self._albums

    def search_tracks(self, q, limit=10):
        if 'tracks' in self._fail:
            raise RuntimeError("tracks boom")
        return self._tracks


# ---------------------------------------------------------------------------
# search_kind
# ---------------------------------------------------------------------------

def test_search_kind_artists_returns_normalized_dicts():
    client = _Client(artists=[_Artist('id1', 'Pink Floyd', 'thumb.jpg', {'spotify': 'url'})])
    result = sources.search_kind(client, 'pink', 'artists', 'spotify')
    assert result == [{
        'id': 'id1',
        'name': 'Pink Floyd',
        'image_url': 'thumb.jpg',
        'external_urls': {'spotify': 'url'},
    }]


def test_search_kind_artists_handles_none_external_urls():
    client = _Client(artists=[_Artist('id1', 'X', None, None)])
    result = sources.search_kind(client, 'x', 'artists')
    assert result[0]['external_urls'] == {}


def test_search_kind_albums_joins_multiple_artists():
    client = _Client(albums=[_Album('a1', 'DSOTM', artists=['Pink Floyd', 'Roger'])])
    result = sources.search_kind(client, 'd', 'albums')
    assert result[0]['artist'] == 'Pink Floyd, Roger'


def test_search_kind_albums_handles_no_artists():
    client = _Client(albums=[_Album('a1', 'Mystery', artists=[])])
    result = sources.search_kind(client, 'm', 'albums')
    assert result[0]['artist'] == 'Unknown Artist'


def test_search_kind_tracks_returns_full_shape():
    client = _Client(tracks=[_Track('t1', 'Money', artists=['Pink Floyd'], album='DSOTM',
                                    duration_ms=383000, image_url='m.jpg',
                                    release_date='1973-03-01', external_urls={'a': 'b'})])
    result = sources.search_kind(client, 'money', 'tracks')
    assert result == [{
        'id': 't1',
        'name': 'Money',
        'artist': 'Pink Floyd',
        'album': 'DSOTM',
        'duration_ms': 383000,
        'image_url': 'm.jpg',
        'release_date': '1973-03-01',
        'external_urls': {'a': 'b'},
    }]


def test_search_kind_swallows_artist_errors():
    client = _Client(fail={'artists'})
    assert sources.search_kind(client, 'q', 'artists') == []


def test_search_kind_swallows_album_errors():
    client = _Client(fail={'albums'})
    assert sources.search_kind(client, 'q', 'albums') == []


def test_search_kind_swallows_track_errors():
    client = _Client(fail={'tracks'})
    assert sources.search_kind(client, 'q', 'tracks') == []


def test_search_kind_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        sources.search_kind(_Client(), 'q', 'movies')


# ---------------------------------------------------------------------------
# search_source — multi-kind executor
# ---------------------------------------------------------------------------

def test_search_source_returns_all_three_kinds():
    client = _Client(
        artists=[_Artist('a', 'A')],
        albums=[_Album('b', 'B', artists=['A'])],
        tracks=[_Track('c', 'C', artists=['A'], album='B')],
    )
    result = sources.search_source('q', client, 'spotify')
    assert result['available'] is True
    assert len(result['artists']) == 1
    assert len(result['albums']) == 1
    assert len(result['tracks']) == 1


def test_search_source_partial_failure_does_not_break_others():
    client = _Client(
        artists=[_Artist('a', 'A')],
        albums=[_Album('b', 'B')],
        tracks=[_Track('c', 'C')],
        fail={'albums'},
    )
    result = sources.search_source('q', client, 'spotify')
    assert result['available'] is True
    assert result['artists'] != []
    assert result['albums'] == []
    assert result['tracks'] != []


def test_search_source_all_fail_returns_empty_lists():
    client = _Client(fail={'artists', 'albums', 'tracks'})
    result = sources.search_source('q', client, 'spotify')
    assert result == {'artists': [], 'albums': [], 'tracks': [], 'available': True}
