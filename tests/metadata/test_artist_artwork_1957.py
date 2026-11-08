"""#1957: HD artist photos and the wide background Plex and Jellyfin show
behind an artist page. Spotify only had a 640px square and nothing ever filled
the background."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

import core.metadata.artist_artwork as art


def _jpeg(w, h):
    out = io.BytesIO()
    Image.new('RGB', (w, h), (120, 80, 40)).save(out, format='JPEG')
    return out.getvalue()


def _png(w, h):
    out = io.BytesIO()
    Image.new('RGB', (w, h), (1, 2, 3)).save(out, format='PNG')
    return out.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None, content=b''):
        self.status_code, self._payload, self.content = status, payload, content
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_photo_must_be_big_and_square_background_wide():
    assert art.fits_photo(_jpeg(1000, 1000))
    assert not art.fits_photo(_jpeg(300, 300))        # no upgrade
    assert not art.fits_photo(_jpeg(1920, 1080))      # a background is not a photo
    assert art.fits_background(_jpeg(1920, 1080))
    assert not art.fits_background(_jpeg(1000, 1000))
    assert not art.fits_background(_jpeg(800, 450))   # too small to fill a page
    assert not art.fits_photo(b'not an image')


def test_fanart_comes_first_most_liked_first(monkeypatch):
    payload = {'artistthumb': [{'url': 'thumb-a', 'likes': '1'}, {'url': 'thumb-b', 'likes': '9'}],
               'artistbackground': [{'url': 'bg-a', 'likes': '3'}]}
    monkeypatch.setattr(art.requests, 'get', lambda url, **k: _Resp(payload=payload))
    monkeypatch.setattr(art, 'fanart_api_key', lambda: 'key')
    monkeypatch.setattr(art, 'audiodb_images', lambda name, mbid: {
        'photos': ['adb-photo'], 'backgrounds': ['adb-fanart']})
    monkeypatch.setattr(art, 'deezer_photo', lambda did: ['dz-xl'] if did else [])
    found = art.candidates('Tycho', mbid='mbid-1', deezer_id='77', fallback_photo='spotify-640')
    assert [u for u, _ in found['photos']] == ['thumb-b', 'thumb-a', 'adb-photo', 'dz-xl', 'spotify-640']
    assert [u for u, _ in found['backgrounds']] == ['bg-a', 'adb-fanart']


def test_no_mbid_no_fanart_and_no_key_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(art.requests, 'get', lambda url, **k: calls.append(url) or _Resp(payload={}))
    assert art.fanart_images('', 'key') == {'photos': [], 'backgrounds': []}
    assert art.fanart_images('mbid', '') == {'photos': [], 'backgrounds': []}
    assert calls == []


def test_audiodb_by_name_only_on_an_exact_name(monkeypatch):
    import core.audiodb_client as adb

    class _Client:
        def lookup_artist_by_mbid(self, mbid):
            return None

        def search_artist(self, name):
            return {'strArtist': 'Tycho Brahe Tribute', 'strArtistThumb': 'wrong-face'}

    monkeypatch.setattr(adb, 'AudioDBClient', lambda: _Client())
    assert art.audiodb_images('Tycho', '') == {'photos': [], 'backgrounds': []}


def test_first_that_fits_skips_what_doesnt_and_hands_back_jpeg(monkeypatch):
    bodies = {'small': _jpeg(200, 200), 'wide': _jpeg(1920, 1080), 'png': _png(1000, 1000)}
    monkeypatch.setattr(art.requests, 'get', lambda url, **k: _Resp(content=bodies[url]))
    picked = art.first_that_fits([('small', 'A'), ('wide', 'B'), ('png', 'C')], art.fits_photo)
    assert picked is not None and picked[1] == 'C'
    assert Image.open(io.BytesIO(picked[0])).format == 'JPEG'


def test_fanart_key_check(monkeypatch):
    monkeypatch.setattr(art.requests, 'get', lambda url, **k: _Resp(status=401))
    assert art.check_fanart_key('bad') == (False, "fanart.tv refused that key.")
    monkeypatch.setattr(art.requests, 'get', lambda url, **k: _Resp(payload={}))
    assert art.check_fanart_key('good')[0] is True
    assert art.check_fanart_key('')[0] is False


# ── the metadata updater ────────────────────────────────────────────────────

class _Server:
    """a media client that records uploads"""

    def __init__(self):
        self.posters, self.backgrounds = [], []

    def is_artist_ignored(self, a):
        return False

    def update_artist_poster(self, artist, data):
        self.posters.append(artist.title)
        return True

    def update_artist_background(self, artist, data):
        self.backgrounds.append(artist.title)
        return True

    @staticmethod
    def artist_has_background(artist):
        return bool(getattr(artist, 'art', None))

    def update_artist_biography(self, artist):
        return True

    def update_artist_genres(self, artist, genres):
        return True


def _worker(server, spotify=None):
    from core.workers.metadata_update import WebMetadataUpdateWorker
    w = WebMetadataUpdateWorker.__new__(WebMetadataUpdateWorker)
    w.media_client, w.spotify_client, w.server_type = server, spotify, 'plex'
    w._db = None
    w.update_album_artwork = lambda artist, sa: 0
    return w


@pytest.fixture
def hd(monkeypatch):
    seen = {}

    def fake_candidates(name, mbid='', deezer_id='', fallback_photo=''):
        seen['ids'] = (mbid, deezer_id)
        return {'photos': [('p', 'fanart.tv')], 'backgrounds': [('b', 'fanart.tv')]}

    monkeypatch.setattr(art, 'candidates', fake_candidates)
    monkeypatch.setattr(art, 'first_that_fits', lambda opts, check: (b'jpg', opts[0][1]) if opts else None)
    return seen


def test_the_updater_fills_photo_and_background_without_spotify(hd):
    server = _Server()
    w = _worker(server)
    w._check_db_artist = lambda name: ({'musicbrainz_id': 'mb-1', 'deezer_id': '9', 'genres': []}, False, None)
    artist = SimpleNamespace(title='Tycho', thumb=None, art=None, genres=['Electronic'])
    ok, details = w.update_artist_metadata(artist)
    assert ok and 'HD photo (fanart.tv)' in details and 'background (fanart.tv)' in details
    assert server.posters == ['Tycho'] and server.backgrounds == ['Tycho']
    assert hd['ids'] == ('mb-1', '9')


def test_existing_artwork_is_never_replaced(hd):
    server = _Server()
    w = _worker(server)
    w._check_db_artist = lambda name: (None, False, None)
    artist = SimpleNamespace(title='Tycho', thumb='/library/metadata/1/thumb/1700000000' + 'x' * 30,
                             art='/library/metadata/1/art/1700000000', genres=['Electronic'])
    w.update_artist_metadata(artist)
    assert server.posters == [] and server.backgrounds == []


def test_jellyfin_counts_a_backdrop_only_when_it_says_so():
    from core.jellyfin_client import JellyfinClient
    has = JellyfinClient.artist_has_background
    assert has(SimpleNamespace(raw={'BackdropImageTags': ['abc']})) is True
    assert has(SimpleNamespace(raw={'BackdropImageTags': []})) is False
    assert has(SimpleNamespace(raw={})) is True   # can't tell: never overwrite


def test_the_artist_lookup_carries_the_ids_artwork_is_tied_to(tmp_path):
    import json
    import sqlite3

    from core.library2.queries import find_artists_by_name
    from core.library2.schema import ensure_library_v2_schema
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (name, name_key, musicbrainz_id, external_ids) VALUES (?, ?, ?, ?)",
                 ('Tycho', 'tycho', 'mb-tycho', json.dumps({'deezer': '1234'})))
    found = find_artists_by_name(conn, 'Tycho')[0]
    assert (found['musicbrainz_id'], found['deezer_id']) == ('mb-tycho', '1234')
