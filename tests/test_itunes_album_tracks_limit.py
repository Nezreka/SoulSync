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
