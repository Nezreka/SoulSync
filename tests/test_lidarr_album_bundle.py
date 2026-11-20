"""Lidarr as an album-bundle source, and the per-album add/search it shares
with the per-track flow.

Lidarr's API is faked at the client boundary (_api_get / _api_post /
_api_delete), so these run without a Lidarr instance.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from core.downloads import album_bundle_dispatch
from core.downloads.track_hint import track_hint_context
from core.lidarr_download_client import LidarrDownloadClient


PISS = {
    'foreignAlbumId': 'rg-piss',
    'title': 'you made me / skin on linen',
    'artist': {'foreignArtistId': 'ar-piss', 'artistName': 'PISS'},
}
DEMOS = {
    'foreignAlbumId': 'rg-demos',
    'title': 'three demos',
    'artist': {'foreignArtistId': 'ar-piss', 'artistName': 'PISS'},
}
OTHER_ARTIST = {
    'foreignAlbumId': 'rg-other',
    'title': 'you made me / skin on linen',
    'artist': {'foreignArtistId': 'ar-other', 'artistName': 'Somebody Else'},
}


class FakeLidarr:
    """A tiny in-memory Lidarr: lookup, artist/album add, queue, track files."""

    def __init__(self, lookup, files=()):
        self.lookup = lookup            # term -> list of album records
        self.files = list(files)        # paths Lidarr "imports"
        self.artists = {}               # foreignArtistId -> id
        self.albums = {}                # foreignAlbumId -> record
        self.commands = []
        self.posts = []
        self.deleted = []
        self.lock = threading.Lock()

    def get(self, endpoint, params=None):
        params = params or {}
        if endpoint == 'album/lookup':
            return self.lookup.get(params['term'], [])
        if endpoint == 'artist':
            mbid = params.get('mbId')
            if mbid in self.artists:
                return [{'id': self.artists[mbid], 'foreignArtistId': mbid}]
            return []
        if endpoint == 'album':
            rec = self.albums.get(params.get('foreignAlbumId'))
            return [rec] if rec else []
        if endpoint.startswith('album/'):
            aid = int(endpoint.split('/')[1])
            for rec in self.albums.values():
                if rec['id'] == aid:
                    return rec
            return None
        if endpoint == 'queue':
            return {'records': []}
        if endpoint == 'trackfile':
            return [{'path': p} for p in self.files]
        if endpoint in ('qualityprofile', 'metadataprofile'):
            return [{'id': 1, 'name': 'Any'}]
        if endpoint == 'rootfolder':
            return [{'path': '/music'}]
        return None

    def post(self, endpoint, data=None):
        with self.lock:
            self.posts.append((endpoint, data))
            if endpoint == 'artist':
                if data['foreignArtistId'] in self.artists:
                    return None  # Lidarr rejects a duplicate artist
                self.artists[data['foreignArtistId']] = len(self.artists) + 1
                return {'id': self.artists[data['foreignArtistId']]}
            if endpoint == 'album':
                if data['foreignAlbumId'] in self.albums:
                    return None  # duplicate album
                rec = {'id': len(self.albums) + 100, 'foreignAlbumId': data['foreignAlbumId'],
                       'monitored': True, 'statistics': {'trackFileCount': 0, 'trackCount': 2}}
                self.albums[data['foreignAlbumId']] = rec
                return rec
            if endpoint == 'command':
                self.commands.append(data)
                # the search "finds" the album: Lidarr imports the files
                for rec in self.albums.values():
                    if rec['id'] in data.get('albumIds', []):
                        rec['statistics']['trackFileCount'] = len(self.files)
                return {'id': 1}
        return None

    def delete(self, endpoint, params=None):
        self.deleted.append(endpoint)
        return True


@pytest.fixture
def client(tmp_path: Path) -> LidarrDownloadClient:
    c = LidarrDownloadClient(download_path=str(tmp_path / 'downloads'))
    c._url, c._api_key = 'http://lidarr', 'key'
    c._cleanup = False
    return c


@contextmanager
def _wired(client, fake):
    with patch.object(client, '_api_get', side_effect=fake.get), \
            patch.object(client, '_api_post', side_effect=fake.post), \
            patch.object(client, '_api_delete', side_effect=fake.delete), \
            patch('core.lidarr_download_client.time.sleep'):
        yield


def _audio(tmp_path: Path, *names):
    paths = []
    for n in names:
        p = tmp_path / 'music' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'flac')
        paths.append(str(p))
    return paths


def test_lidarr_is_an_album_bundle_source():
    assert album_bundle_dispatch.is_eligible(
        mode='lidarr', is_album=True, album_name='π', artist_name='Polar Inertia')


def test_find_album_needs_matching_artist_and_title(client):
    fake = FakeLidarr({
        'PISS you made me / skin on linen': [DEMOS, OTHER_ARTIST, PISS],
    })
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('you made me / skin on linen', 'PISS') is PISS
        assert client._find_album('a fourth demo', 'PISS') is None


def test_find_album_by_release_group_id_first(client):
    fake = FakeLidarr({'lidarr:rg-piss': [PISS]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('anything', 'anyone', 'rg-piss') is PISS


def test_find_album_keeps_non_latin_titles(client):
    pi = {'foreignAlbumId': 'rg-pi', 'title': 'π',
          'artist': {'foreignArtistId': 'ar-pi', 'artistName': 'Polar Inertia'}}
    fake = FakeLidarr({'Polar Inertia π': [pi]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('π', 'Polar Inertia') is pi


def test_album_bundle_stages_every_imported_file(client, tmp_path):
    files = _audio(tmp_path, '01 you made me.flac', '02 skin on linen.flac')
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]}, files)
    staging = tmp_path / 'staging'
    states = []
    with _wired(client, fake):
        out = client.download_album_to_staging(
            'you made me / skin on linen', 'PISS', str(staging), lambda p: states.append(p['state']))
    assert out['success'], out
    assert sorted(Path(f).name for f in out['files']) == ['01 you made me.flac', '02 skin on linen.flac']
    assert out['expected_count'] == 2 and not out['partial']
    assert [c['name'] for c in fake.commands] == ['AlbumSearch']
    assert states[0] == 'searching' and 'staging' in states


def test_album_bundle_miss_falls_back_only_in_hybrid(client, tmp_path):
    fake = FakeLidarr({})
    cfg = {'download_source.mode': 'lidarr'}
    with patch.object(client, '_api_get', side_effect=fake.get), \
            patch('core.lidarr_download_client.config_manager.get',
                  side_effect=lambda k, d=None: cfg.get(k, d)):
        out = client.download_album_to_staging('Nope', 'Nobody', str(tmp_path / 's'))
        assert not out['success'] and out['fallback'] is False
        cfg.update({'download_source.mode': 'hybrid', 'download_source.hybrid_order': ['lidarr', 'soulseek']})
        out = client.download_album_to_staging('Nope', 'Nobody', str(tmp_path / 's'))
        assert not out['success'] and out['fallback'] is True


def test_parallel_tracks_add_the_album_once(client, tmp_path):
    """The tracks of one album used to race: each added the artist, all but
    one failed, and those tracks reported 'Failed to add album to Lidarr'."""
    fake = FakeLidarr({}, _audio(tmp_path, '01.flac'))
    results = []
    with _wired(client, fake):
        threads = [threading.Thread(target=lambda: results.append(client._ensure_album_searched(PISS)))
                   for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    ids = {r[0] for r in results}
    assert len(ids) == 1 and None not in ids, results
    assert [e for e, _ in fake.posts].count('artist') == 1
    assert [e for e, _ in fake.posts].count('album') == 1
    assert len(fake.commands) == 1


def test_existing_artist_is_reused_when_add_fails(client):
    fake = FakeLidarr({})
    fake.artists['ar-piss'] = 7
    with _wired(client, fake):
        assert client._ensure_artist(PISS['artist']) == 7
    assert not [e for e, _ in fake.posts if e == 'artist']


def test_wait_reports_not_grabbed_after_the_grab_window(client):
    fake = FakeLidarr({})
    fake.albums['rg-piss'] = {'id': 100, 'foreignAlbumId': 'rg-piss', 'statistics': {'trackFileCount': 0}}
    clock = iter(range(0, 10_000, 100))
    with patch.object(client, '_api_get', side_effect=fake.get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=lambda: next(clock)):
        assert client._wait_for_album(100, 'rg-piss') == 'not_grabbed'


def test_per_track_search_looks_up_the_hinted_album(client):
    """A track query ('PISS skin on linen') does not find the album in a
    release-group search; the task's album hint does."""
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS], 'PISS skin on linen': [DEMOS]})
    hint = {'title': 'skin on linen', 'artist': 'PISS', 'album': 'you made me / skin on linen'}
    with patch.object(client, '_api_get', side_effect=fake.get), track_hint_context(hint):
        tracks, _ = client._search_sync('PISS skin on linen')
    assert [t.album for t in tracks] == ['you made me / skin on linen', 'three demos']


def test_albums_only_skips_per_track_search(client):
    import asyncio
    client._albums_only = True
    with patch.object(client, '_search_sync', side_effect=AssertionError('searched')):
        assert asyncio.run(client.search('PISS skin on linen')) == ([], [])


def test_album_bundle_uses_the_release_group_id(client, tmp_path):
    """Text lookups for PISS return other bands' albums; the wishlist's
    MusicBrainz release group finds the right one."""
    files = _audio(tmp_path, '01.flac')
    fake = FakeLidarr({'lidarr:rg-piss': [PISS], 'PISS you made me / skin on linen': [OTHER_ARTIST]}, files)
    with _wired(client, fake):
        out = client.download_album_to_staging(
            'you made me / skin on linen', 'PISS', str(tmp_path / 's'), release_group_id='rg-piss')
    assert out['success'], out
    assert 'rg-piss' in fake.albums


def test_release_group_id_of_requires_agreement():
    from core.downloads.master import release_group_id_of
    assert release_group_id_of([{'musicbrainz_release_group_id': 'a'},
                                {'musicbrainz_release_group_id': None}]) == 'a'
    assert release_group_id_of([{'musicbrainz_release_group_id': 'a'},
                                {'musicbrainz_release_group_id': 'b'}]) is None
    assert release_group_id_of([{}]) is None
