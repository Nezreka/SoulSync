"""iTunes album-track fetch must request the full album, not the 50-entity default (#918).

The iTunes Lookup API returns only 50 related entities unless `limit` is passed (max 200),
so albums >50 tracks were truncated to the first 50 in the download window. get_album_tracks
must pass limit=200.
"""

from __future__ import annotations

from core import itunes_client as ic


class _Cache:
    """Cache stub: always a miss; any write method is a no-op."""
    def get_entity(self, *a, **k):
        return None

    def __getattr__(self, _name):
        return lambda *a, **k: None


_RESULTS = [
    {'wrapperType': 'collection', 'collectionId': 123, 'collectionName': 'Big OST',
     'artistName': 'Composer', 'trackCount': 70, 'artworkUrl100': 'http://x/100x100bb.jpg'},
    {'wrapperType': 'track', 'kind': 'song', 'trackId': 1, 'trackName': 'Track 1',
     'trackNumber': 1, 'discNumber': 1, 'artistName': 'Composer', 'trackTimeMillis': 1000},
]


def test_get_album_tracks_requests_limit_200(monkeypatch):
    client = ic.iTunesClient(country='US')
    captured = {}

    def fake_lookup(**params):
        captured.update(params)
        return _RESULTS

    monkeypatch.setattr(client, '_lookup', fake_lookup)
    monkeypatch.setattr(ic, 'get_metadata_cache', lambda: _Cache())

    result = client.get_album_tracks('123')

    assert captured.get('limit') == 200          # #918: full album, not the iTunes 50 default
    assert captured.get('entity') == 'song'
    assert captured.get('id') == '123'
    assert result is not None
    assert result.get('_complete') is True       # fresh fetch is marked complete


# ── #918 follow-up: self-heal a stale truncated cache ─────────────────────────
# The metadata cache is persistent (30-day TTL), so a tracks entry written before
# the limit=200 fix stays truncated at 50 and is served to EVERY window (e.g. the
# Standard-view add-album modal) until it expires. get_album_tracks must detect a
# legacy entry shorter than the album's known trackCount and re-fetch.

class _StubCache:
    """Holds entities so cache hits/stores can be asserted."""
    def __init__(self, entities=None):
        self.entities = dict(entities or {})
        self.stored = {}

    def get_entity(self, source, entity_type, entity_id):
        return self.entities.get((source, entity_type, entity_id))

    def store_entity(self, source, entity_type, entity_id, data):
        self.stored[(source, entity_type, entity_id)] = data

    def store_entities_bulk(self, *a, **k):
        pass


def _collection(track_count):
    return {'wrapperType': 'collection', 'collectionId': 123, 'collectionName': 'Big OST',
            'artistName': 'Composer', 'trackCount': track_count, 'artworkUrl100': 'http://x/100x100bb.jpg'}


def _track(n):
    return {'wrapperType': 'track', 'kind': 'song', 'trackId': n, 'trackName': f'T{n}',
            'trackNumber': n, 'discNumber': 1, 'artistName': 'Composer', 'trackTimeMillis': 1000}


def _full_results(n):
    return [_collection(n)] + [_track(i) for i in range(1, n + 1)]


def _items(n):
    return [{'id': str(i)} for i in range(n)]


def test_stale_truncated_legacy_cache_is_refetched(monkeypatch):
    """A 50-item legacy entry (no _complete) for a 70-track album → re-fetch full."""
    client = ic.iTunesClient(country='US')
    cache = _StubCache({
        ('itunes', 'album', '123_tracks'): {'items': _items(50), 'total': 50},   # legacy, truncated
        ('itunes', 'album', '123'): _collection(70),                              # real trackCount=70
    })
    monkeypatch.setattr(ic, 'get_metadata_cache', lambda: cache)
    calls = {'n': 0}

    def fake_lookup(**params):
        calls['n'] += 1
        return _full_results(70)

    monkeypatch.setattr(client, '_lookup', fake_lookup)

    result = client.get_album_tracks('123')

    assert calls['n'] == 1                                # re-fetched (didn't trust the stale 50)
    assert len(result['items']) == 70
    assert result['_complete'] is True
    assert cache.stored[('itunes', 'album', '123_tracks')]['_complete'] is True   # healed in cache


def test_complete_cache_is_trusted_no_refetch(monkeypatch):
    """A _complete entry is returned as-is even if shorter than trackCount
    (region-restricted album) — must NOT loop re-fetching."""
    client = ic.iTunesClient(country='US')
    complete = {'items': _items(50), 'total': 50, '_complete': True}
    cache = _StubCache({
        ('itunes', 'album', '123_tracks'): complete,
        ('itunes', 'album', '123'): _collection(70),
    })
    monkeypatch.setattr(ic, 'get_metadata_cache', lambda: cache)

    def boom(**params):
        raise AssertionError('must not re-fetch a _complete entry')

    monkeypatch.setattr(client, '_lookup', boom)

    assert client.get_album_tracks('123') is complete


def test_legacy_complete_cache_not_refetched(monkeypatch):
    """A legacy entry whose length already meets trackCount is fine — no re-fetch."""
    client = ic.iTunesClient(country='US')
    legacy = {'items': _items(30), 'total': 30}          # no _complete, but complete by count
    cache = _StubCache({
        ('itunes', 'album', '123_tracks'): legacy,
        ('itunes', 'album', '123'): _collection(30),
    })
    monkeypatch.setattr(ic, 'get_metadata_cache', lambda: cache)
    monkeypatch.setattr(client, '_lookup', lambda **p: (_ for _ in ()).throw(AssertionError('no refetch')))

    assert client.get_album_tracks('123') is legacy


def test_legacy_cache_without_album_meta_is_trusted(monkeypatch):
    """trackCount unknown (album meta not cached) → trust the cache, don't re-fetch
    (safe fallback; no regression for direct get_album_tracks callers)."""
    client = ic.iTunesClient(country='US')
    legacy = {'items': _items(50), 'total': 50}          # no _complete, no album meta
    cache = _StubCache({('itunes', 'album', '123_tracks'): legacy})
    monkeypatch.setattr(ic, 'get_metadata_cache', lambda: cache)
    monkeypatch.setattr(client, '_lookup', lambda **p: (_ for _ in ()).throw(AssertionError('no refetch')))

    assert client.get_album_tracks('123') is legacy
