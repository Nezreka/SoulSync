"""last.fm radio endpoint guards - from the aug 25 breakage report."""

import os
import tempfile

import pytest

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    web_server.app.config['TESTING'] = True
    return web_server.app.test_client()


class _FakeLastfm:
    api_key = 'k'

    def _make_request(self, method, params):
        return {'results': {'trackmatches': {'track': [
            # listeners as '' - the row that used to 500 the WHOLE search
            {'name': 'Ghost Town', 'artist': 'A', 'listeners': '',
             'image': [{'#text': 'http://img/2a96cbd8b46e442fc41c2b86b821562f.png',
                        'size': 'large'}]},
            {'name': 'Real Art', 'artist': 'B', 'listeners': '42',
             'image': [{'#text': 'http://img/real.jpg', 'size': 'large'}]},
        ]}}}

    def get_best_image(self, images):
        for i in reversed(images or []):
            if i.get('#text'):
                return i['#text']
        return ''


class _FakeWorker:
    client = _FakeLastfm()


def test_search_survives_blank_listeners_and_hides_the_placeholder_star(client, monkeypatch):
    monkeypatch.setattr(web_server, 'lastfm_worker', _FakeWorker())
    r = client.get('/api/lastfm/search/tracks?q=ghost')
    assert r.status_code == 200
    rows = r.get_json()['results']
    assert len(rows) == 2
    ghost = rows[0]
    assert ghost['listeners'] == 0            # int('') used to raise here
    assert ghost['image_url'] == ''           # the grey star is not art
    assert rows[1]['image_url'] == 'http://img/real.jpg'


def test_reading_a_trackless_radio_never_deletes_it(client, monkeypatch):
    """a cache-miss read used to delete-then-refetch; a lastfm pseudo-mbid
    can't be refetched from the LB api, so the read DESTROYED the playlist."""
    deleted = []

    class _SpyManager:
        class client:  # noqa: N801 - shape of the real manager
            @staticmethod
            def is_authenticated():
                return True

            @staticmethod
            def get_playlist_details(mbid):
                raise AssertionError('must not hit the LB api for a pseudo-mbid')

        @staticmethod
        def get_cached_tracks(mbid):
            return []

        @staticmethod
        def get_playlist_type(mbid):
            return 'lastfm_radio'

        @staticmethod
        def delete_cached_playlist(mbid):
            deleted.append(mbid)

    monkeypatch.setattr(web_server, '_get_profile_lb_manager',
                        lambda: (_SpyManager(), 'u', 'lb'))
    r = client.get('/api/discover/listenbrainz/playlist/lastfm_radio_abc123def456')
    assert r.status_code == 404
    assert 'regenerate' in r.get_json()['error']
    assert deleted == []
