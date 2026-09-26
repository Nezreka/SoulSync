"""/api/library/albums — the one endpoint behind the library's album view.

The mirror of /api/library/artists, down to its failure shape: the grid
renders from `albums` and the pager from `pagination`, so a 500 still has
to carry both or the page throws instead of showing its empty state.

Album art needs the same normalization the artist grid already does for
artists and the artist page already does for albums — a media-server
relative path is not something a browser can load.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-libalbums-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'libalbums.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

PAGINATION = {'page': 1, 'limit': 75, 'total_count': 1, 'total_pages': 1, 'has_prev': False, 'has_next': False}


class FakeDatabase:
    def __init__(self, result=None, boom=False):
        self.result = result or {'albums': [], 'pagination': PAGINATION}
        self.boom = boom
        self.calls = []

    def get_library_albums(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise RuntimeError('database is gone')
        return self.result


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def fake_db(monkeypatch):
    def install(result=None, boom=False):
        db = FakeDatabase(result, boom)
        monkeypatch.setattr(web_server, 'get_database', lambda: db)
        return db

    return install


def test_returns_the_albums_and_the_pager(client, fake_db):
    fake_db({'albums': [{'id': 'al1', 'title': 'Parklife', 'artist_name': 'Blur'}], 'pagination': PAGINATION})

    body = client.get('/api/library/albums').get_json()

    assert body['success'] is True
    assert body['albums'] == [{'id': 'al1', 'title': 'Parklife', 'artist_name': 'Blur'}]
    assert body['pagination'] == PAGINATION


def test_passes_the_two_filters_the_album_view_offers(client, fake_db):
    db = fake_db()

    client.get('/api/library/albums?search=park&letter=p&page=3&limit=20')

    assert db.calls[0]['search_query'] == 'park'
    assert db.calls[0]['letter'] == 'p'
    assert db.calls[0]['page'] == 3
    assert db.calls[0]['limit'] == 20


def test_normalizes_media_server_art_paths(client, fake_db, monkeypatch):
    """The suite runs with no media-server config, so the real normalizer hands
    back every path unchanged — which would make a "the path was rewritten"
    assertion pass with the normalization hop deleted. Standing in for it is
    what makes the hop observable; the assertion is still on the response."""
    fake_db({'albums': [{'id': 'al1', 'thumb_url': '/library/metadata/123/thumb/456'}], 'pagination': PAGINATION})
    monkeypatch.setattr(web_server, 'fix_artist_image_url', lambda url: f'/proxy?u={url}')

    thumb = client.get('/api/library/albums').get_json()['albums'][0]['thumb_url']

    assert thumb == '/proxy?u=/library/metadata/123/thumb/456'


def test_leaves_an_album_with_no_art_alone(client, fake_db):
    fake_db({'albums': [{'id': 'al1', 'thumb_url': None}], 'pagination': PAGINATION})

    assert client.get('/api/library/albums').get_json()['albums'][0]['thumb_url'] is None


def test_a_failure_still_carries_the_shape_the_page_reads(client, fake_db):
    fake_db(boom=True)

    response = client.get('/api/library/albums')
    body = response.get_json()

    assert response.status_code == 500
    assert body['success'] is False
    assert body['albums'] == []
    assert body['pagination']['total_count'] == 0


def test_passes_the_source_filter_through(client, fake_db):
    db = fake_db()

    client.get('/api/library/albums?source_filter=musicbrainz')

    assert db.calls[0]['source_filter'] == 'musicbrainz'


def test_defaults_the_source_filter_to_no_filter(client, fake_db):
    # The page omits the param when no source is chosen, as the artists view
    # does, so the handler has to supply the empty default itself.
    db = fake_db()

    client.get('/api/library/albums')

    assert db.calls[0]['source_filter'] == ''


# ── the play button's tracklist ──────────────────────────────────────────────
# /api/album/<id>/tracks resolves a METADATA SOURCE's tracklist, for the
# download-missing modal. Playing an album you own needs the opposite: the
# rows that have a file, so the player queues them instead of treating each
# one as a miss to download.

class FakeTrack:
    def __init__(self, track_id, title, number, path, duration=None, bitrate=None):
        self.id, self.title, self.track_number = track_id, title, number
        self.file_path, self.duration, self.bitrate = path, duration, bitrate


def test_returns_the_owned_tracks_in_disc_order(client, monkeypatch):
    class DB:
        def get_tracks_by_album(self, album_id):
            assert album_id == 'al1'
            return [FakeTrack('t1', 'Girls & Boys', 1, '/m/1.flac', 260000, 1000)]

    monkeypatch.setattr(web_server, 'get_database', lambda: DB())

    body = client.get('/api/library/albums/al1/tracks').get_json()

    assert body['success'] is True
    assert body['tracks'] == [
        {
            'id': 't1',
            'title': 'Girls & Boys',
            'track_number': 1,
            'file_path': '/m/1.flac',
            'duration': 260000,
            'bitrate': 1000,
        }
    ]


def test_leaves_out_a_row_with_no_file_because_it_cannot_be_played(client, monkeypatch):
    class DB:
        def get_tracks_by_album(self, album_id):
            return [FakeTrack('t1', 'Owned', 1, '/m/1.flac'), FakeTrack('t2', 'Missing', 2, '')]

    monkeypatch.setattr(web_server, 'get_database', lambda: DB())

    titles = [t['title'] for t in client.get('/api/library/albums/al1/tracks').get_json()['tracks']]

    assert titles == ['Owned']


def test_an_album_with_nothing_playable_says_so_rather_than_failing(client, monkeypatch):
    class DB:
        def get_tracks_by_album(self, album_id):
            return []

    monkeypatch.setattr(web_server, 'get_database', lambda: DB())

    response = client.get('/api/library/albums/al1/tracks')

    assert response.status_code == 200
    assert response.get_json() == {'success': True, 'tracks': []}
