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


# ---------------------------------------------------------------------------
# "Whole album via Lidarr" and where Lidarr-sourced tracks land
# ---------------------------------------------------------------------------


def _cfg(values):
    return lambda key, default=None: values.get(key, default)


def test_whole_album_choice_only_matters_in_hybrid():
    from core.downloads.album_bundle_dispatch import lidarr_whole_album
    assert lidarr_whole_album({'lidarr_whole_album': False}, _cfg({'download_source.mode': 'lidarr'}))
    hybrid = {'download_source.mode': 'hybrid'}
    assert not lidarr_whole_album({'lidarr_whole_album': False}, _cfg(hybrid))
    assert lidarr_whole_album({'lidarr_whole_album': True}, _cfg(hybrid))


def test_whole_album_default_applies_without_a_choice():
    from core.downloads.album_bundle_dispatch import lidarr_whole_album
    cfg = {'download_source.mode': 'hybrid'}
    assert lidarr_whole_album({}, _cfg(cfg))  # default on
    cfg['lidarr_download.whole_album_default'] = False
    assert not lidarr_whole_album({}, _cfg(cfg))
    assert not lidarr_whole_album({'lidarr_whole_album': None}, _cfg(cfg))
    assert lidarr_whole_album({'lidarr_whole_album': True}, _cfg(cfg))


def test_lidarr_tracks_land_in_the_lidarr_folder(tmp_path):
    from core.imports import paths
    values = {'lidarr_download.transfer_path': str(tmp_path / 'lidarr-imports'),
              'soulseek.transfer_path': str(tmp_path / 'library')}
    fake = type('C', (), {'get': staticmethod(_cfg(values))})
    with patch.object(paths, '_get_config_manager', return_value=fake):
        lidarr_ctx = {'original_search_result': {'username': 'lidarr'}}
        peer_ctx = {'original_search_result': {'username': 'some-soulseek-peer'}}
        assert paths.lidarr_root_for_context(lidarr_ctx) == str(tmp_path / 'lidarr-imports')
        assert paths.lidarr_root_for_context(peer_ctx) is None
        values['lidarr_download.transfer_path'] = ''
        assert paths.lidarr_root_for_context(lidarr_ctx) is None


def test_settings_reload_keeps_lidarrs_own_download_path(tmp_path):
    """The orchestrator used to copy soulseek.download_path onto every
    source on reload, so Lidarr's copies landed in slskd's folder."""
    from core import download_orchestrator as orch_mod
    values = {'soulseek.download_path': str(tmp_path / 'slskd'),
              'lidarr_download.download_path': str(tmp_path / 'lidarr')}
    lidarr = LidarrDownloadClient(download_path=str(tmp_path / 'old'))
    other = type('Other', (), {'download_path': tmp_path / 'old'})()

    class Registry:
        def all_plugins(self):
            return [('lidarr', lidarr), ('youtube', other)]

        def display_name(self, name):
            return name

    orch = orch_mod.DownloadOrchestrator.__new__(orch_mod.DownloadOrchestrator)
    orch.registry = Registry()
    orch.client = lambda name: None
    get = lambda key, default=None: values.get(key, default)
    with patch.object(orch_mod.config_manager, 'get', side_effect=get), \
            patch('core.lidarr_download_client.config_manager.get', side_effect=get):
        orch.reload_settings()
    assert lidarr.download_path == tmp_path / 'lidarr'
    assert other.download_path == tmp_path / 'slskd'


# ---------------------------------------------------------------------------
# review fixes: waiting through blips, stuck imports, staging, shared albums
# ---------------------------------------------------------------------------


def _clock(step=2):
    ticks = iter(range(0, 10_000_000, step))
    return lambda: next(ticks)


def test_wait_survives_a_lidarr_blip(client):
    """one failed album read after 30 s used to end a 6 h wait as 'gone'."""
    reads = {'n': 0}
    album = {'id': 100, 'statistics': {'trackFileCount': 0}}

    def get(endpoint, params=None):
        if endpoint == 'queue':
            return {'records': [{'album': {'foreignAlbumId': 'rg'}, 'status': 'downloading',
                                 'size': 10, 'sizeleft': 5}]} if reads['n'] < 40 else {'records': []}
        reads['n'] += 1
        if 20 <= reads['n'] < 25:
            return None  # lidarr restarting
        if reads['n'] >= 40:
            album['statistics']['trackFileCount'] = 2
        return album

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock()):
        assert client._wait_for_album(100, 'rg') == 'complete'


def test_wait_still_reports_a_deleted_album(client):
    def get(endpoint, params=None):
        if endpoint == 'queue':
            return {'records': [{'album': {'foreignAlbumId': 'rg'}, 'status': 'downloading'}]}
        return None

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock()):
        assert client._wait_for_album(100, 'rg') == 'gone'


def test_an_unreadable_queue_is_not_an_empty_one(client):
    """lidarr imports file by file. a failed queue read used to look like
    "nothing queued", and an album with 1 of 12 files in was staged as done."""
    polls = {'n': 0}

    def get(endpoint, params=None):
        if endpoint == 'queue':
            polls['n'] += 1
            if polls['n'] > 5:
                return {'records': []}
            if polls['n'] % 2:
                return None
            return {'records': [{'album': {'foreignAlbumId': 'rg'}, 'status': 'downloading'}]}
        return {'id': 100, 'statistics': {'trackFileCount': 1 if polls['n'] <= 5 else 12}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock()):
        assert client._wait_for_album(100, 'rg') == 'complete'
    assert polls['n'] > 5


def test_wait_gives_up_on_an_import_lidarr_cannot_finish(client):
    def get(endpoint, params=None):
        if endpoint == 'queue':
            return {'records': [{'album': {'foreignAlbumId': 'rg'}, 'status': 'completed',
                                 'trackedDownloadState': 'importBlocked'}]}
        return {'id': 100, 'statistics': {'trackFileCount': 0}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock()):
        assert client._wait_for_album(100, 'rg') == 'import_failed'


def test_staging_keeps_two_discs_same_named_files(client, tmp_path):
    disc1 = tmp_path / 'music' / 'CD1' / '01 - Intro.flac'
    disc2 = tmp_path / 'music' / 'CD2' / '01 - Intro.flac'
    for p, body in ((disc1, b'one'), (disc2, b'two')):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]}, [str(disc1), str(disc2)])
    with _wired(client, fake):
        out = client.download_album_to_staging('you made me / skin on linen', 'PISS',
                                               str(tmp_path / 'staging'))
    assert out['success'], out
    assert sorted(Path(f).read_bytes() for f in out['files']) == [b'one', b'two']
    assert not list((tmp_path / 'staging').glob('*.tmp*'))


def test_failed_bundle_ends_on_a_failed_state(client, tmp_path):
    states = []
    fake = FakeLidarr({})
    cfg = {'download_source.mode': 'lidarr'}
    with patch.object(client, '_api_get', side_effect=fake.get), \
            patch('core.lidarr_download_client.config_manager.get',
                  side_effect=lambda k, d=None: cfg.get(k, d)):
        out = client.download_album_to_staging('Nope', 'Nobody', str(tmp_path / 's'),
                                               lambda p: states.append(p['state']))
    assert not out['success']
    assert states[-1] == 'failed'


def test_cleanup_waits_for_every_bundle_on_the_album(client, tmp_path):
    """two batches wanting one album: the first to finish deleted the album
    from lidarr, and the second read that as 'Album was removed from Lidarr'."""
    client._cleanup = True
    files = _audio(tmp_path, '01.flac')
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]}, files)
    with _wired(client, fake):
        client._bundle_waits['rg-piss'] = 1  # another batch is still waiting
        out = client.download_album_to_staging('you made me / skin on linen', 'PISS',
                                               str(tmp_path / 's'))
        assert out['success'], out
        assert fake.deleted == []
        assert client._bundle_waits == {'rg-piss': 1}
        client._bundle_waits.clear()
        out = client.download_album_to_staging('you made me / skin on linen', 'PISS',
                                               str(tmp_path / 's2'))
        assert out['success'], out
        assert fake.deleted == ['album/100']
    assert client._bundle_waits == {}


def test_find_album_looks_past_an_edition_note(client):
    rumours = {'foreignAlbumId': 'rg-rumours', 'title': 'Rumours',
               'artist': {'foreignArtistId': 'ar-fm', 'artistName': 'Fleetwood Mac'}}
    live = {'foreignAlbumId': 'rg-live', 'title': 'Rumours',
            'artist': {'foreignArtistId': 'ar-fm', 'artistName': 'Fleetwood Mac'}}
    fake = FakeLidarr({'Fleetwood Mac Rumours (Deluxe Edition)': [rumours]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('Rumours (Deluxe Edition)', 'Fleetwood Mac') is rumours
    fake = FakeLidarr({'Fleetwood Mac Rumours (Live)': [live]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('Rumours (Live)', 'Fleetwood Mac') is None
