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
        self.library = []               # albums already in Lidarr, with artist ids
        self.queue = []                 # queue records
        self.manual = []                # what Manual Import lists for a folder
        self.manual_folders = []
        self.landed = []                # track files Manual Import created
        self.track_files = {}           # track id -> track file id
        self.turned_down = set()        # paths Lidarr rejects while importing

    def get(self, endpoint, params=None):
        params = params or {}
        if endpoint == 'album/lookup':
            return self.lookup.get(params['term'], [])
        if endpoint == 'artist':
            mbid = params.get('mbId')
            if mbid is None:
                return [dict(a['artist']) for a in self.library]
            if mbid in self.artists:
                return [{'id': self.artists[mbid], 'foreignArtistId': mbid}]
            return []
        if endpoint == 'album' and 'artistId' in params:
            return [a for a in self.library if a['artist']['id'] == params['artistId']]
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
            return {'records': list(self.queue)}
        if endpoint == 'manualimport':
            self.manual_folders.append(params['folder'])
            return [m for m in self.manual if m['path'].startswith(params['folder'] + '/')]
        if endpoint == 'trackfile':
            return [{'path': p} for p in self.files] + list(self.landed)
        if endpoint == 'track':
            return [{'id': t, 'trackFileId': f} for t, f in self.track_files.items()]
        if endpoint == 'command/77':
            return {'id': 77, 'status': 'completed', 'started': '2026-11-22T00:22:52.400Z'}
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
                       'artistId': data['artistId'], 'monitored': data['monitored'],
                       'statistics': {'trackFileCount': 0, 'trackCount': 2}}
                self.albums[data['foreignAlbumId']] = rec
                return rec
            if endpoint == 'command' and data.get('name') == 'ManualImport':
                self.commands.append(data)
                for f in data['files']:
                    if f['path'] in self.turned_down:
                        continue
                    file_id = 500 + len(self.landed)
                    self.landed.append({'id': file_id, 'dateAdded': '2026-11-22T00:22:52Z',
                                        'path': '/music/PISS/album/' + f['path'].rsplit('/', 1)[1]})
                    for track_id in f['trackIds']:
                        self.track_files[track_id] = file_id
                return {'id': 77, 'status': 'queued', 'queued': '2026-11-22T00:22:52.100Z'}
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
        if endpoint.startswith('queue/'):
            self.queue = [r for r in self.queue if f"queue/{r['id']}" != endpoint]
            assert params == {'removeFromClient': 'true', 'blocklist': 'true'}
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


def test_find_album_prefers_lidarrs_own_library(client):
    # Lidarr's metadata search buries a short artist name under other
    # artists' records; the album Lidarr already has is found by name.
    in_library = dict(PISS, id=2412, artist=dict(PISS['artist'], id=7))
    fake = FakeLidarr({'PISS you made me / skin on linen': [DEMOS, OTHER_ARTIST]})
    fake.library = [in_library]
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('you made me / skin on linen', 'PISS') is in_library
        assert client._find_album('a fourth demo', 'PISS') is None


def _af(year, rg):
    return {'foreignAlbumId': rg, 'title': 'American Football', 'releaseDate': f'{year}-01-01T00:00:00Z',
            'artist': {'foreignArtistId': 'ar-af', 'artistName': 'American Football'}}


def test_same_titled_albums_are_told_apart_by_year(client):
    fake = FakeLidarr({'American Football American Football': [_af(1999, 'lp1'), _af(2016, 'lp2'), _af(2019, 'lp3')]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('American Football', 'American Football', '', '2016')['foreignAlbumId'] == 'lp2'
        # no record from that year: guessing would grab another album
        assert client._find_album('American Football', 'American Football', '', '2026') is None


def test_library_match_from_another_year_is_not_trusted(client):
    # Lidarr holds only the 2019 record; a 2026 request must not take it.
    lp3 = dict(_af(2019, 'lp3'), id=5, artist={'id': 3, 'artistName': 'American Football'})
    fake = FakeLidarr({'American Football American Football': [_af(1999, 'lp1'), _af(2019, 'lp3')]})
    fake.library = [lp3]
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('American Football', 'American Football', '', '2026') is None
        assert client._find_album('American Football', 'American Football', '', '2019') is lp3


def test_a_lone_match_from_another_year_is_a_reissue(client):
    fake = FakeLidarr({'PISS you made me / skin on linen': [dict(PISS, releaseDate='2025-11-01')]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('you made me / skin on linen', 'PISS', '', '2026')['foreignAlbumId'] == PISS['foreignAlbumId']


def test_lookup_retries_a_failed_metadata_search(client):
    answers = [None, [PISS]]
    with patch.object(client, '_api_get', side_effect=lambda *a, **k: answers.pop(0)), \
            patch('core.lidarr_download_client.time.sleep'):
        assert client._lookup('PISS you made me / skin on linen') == [PISS]


def test_bundle_release_year_of_requires_agreement():
    from core.downloads.master import bundle_release_year_of
    assert bundle_release_year_of([{'album': {'release_date': '2026-05-01'}}, {'album': {}}]) == '2026'
    assert bundle_release_year_of([{'album': {'release_date': '2026-05-01'}},
                                   {'album': {'release_date': '2019-03-22'}}]) is None


def test_the_album_context_year_is_still_read():
    # the bundle helper first shipped as a second release_year_of, which
    # replaced this one for every caller: the re-release gate read no year
    from core.downloads.master import release_year_of
    assert release_year_of({'name': 'Chaosphere', 'release_date': '2023-11-10'}) == '2023'
    assert release_year_of({'year': 1998}) == '1998'


def test_bundle_release_type_of_requires_agreement():
    from core.downloads.master import bundle_release_type_of
    assert bundle_release_type_of([{'album': {'album_type': 'Album'}}, {'album': {}}]) == 'album'
    assert bundle_release_type_of([{'album': {'album_type': 'album'}},
                                   {'album': {'album_type': 'single'}}]) is None


def _bib(rg, kind):
    return {'foreignAlbumId': rg, 'title': 'Back in Black', 'albumType': kind,
            'releaseDate': '1980-07-25T00:00:00Z',
            'artist': {'foreignArtistId': 'ar-acdc', 'artistName': 'AC/DC'}}


def test_a_reissue_year_still_finds_the_album_over_its_title_single(client):
    # spotify dates the remaster 2003; musicbrainz knows the 1980 album and
    # the 1980 single. no year fits, so the album type picks the album.
    fake = FakeLidarr({'AC/DC Back in Black': [_bib('rg-single', 'Single'), _bib('rg-album', 'Album')]})
    with patch.object(client, '_api_get', side_effect=fake.get):
        found = client._find_album('Back in Black', 'AC/DC', '', '2003', release_type='album')
        assert found['foreignAlbumId'] == 'rg-album'
        assert client._find_album('Back in Black', 'AC/DC', '', '1980', release_type='single'
                                  )['foreignAlbumId'] == 'rg-single'
        # with no type to go by, two records and no year is still a refusal
        assert client._find_album('Back in Black', 'AC/DC', '', '2003') is None


def test_the_type_does_not_overrule_the_year(client):
    # three self-titled albums: the type fits all of them, the year decides
    records = [dict(_af(y, rg), albumType='Album') for y, rg in ((1999, 'lp1'), (2016, 'lp2'), (2019, 'lp3'))]
    fake = FakeLidarr({'American Football American Football': records})
    with patch.object(client, '_api_get', side_effect=fake.get):
        assert client._find_album('American Football', 'American Football', '', '2026',
                                  release_type='album') is None
        assert client._find_album('American Football', 'American Football', '', '2016',
                                  release_type='album')['foreignAlbumId'] == 'lp2'


def test_the_hinted_album_type_reaches_the_lookup(client):
    fake = FakeLidarr({'AC/DC Back in Black': [_bib('rg-single', 'Single'), _bib('rg-album', 'Album')]})
    hint = {'title': 'Hells Bells', 'artist': 'AC/DC', 'album': 'Back in Black',
            'catalogue_context': {'track_info': {'album': {'release_date': '2003-01-01', 'album_type': 'album'}}}}
    with patch.object(client, '_api_get', side_effect=fake.get), track_hint_context(hint):
        assert [a['foreignAlbumId'] for a in client._hinted_album_lookup()] == ['rg-album']


def test_lidarrs_artist_list_is_read_once_a_minute(client):
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    calls = []

    def get(endpoint, params=None):
        calls.append(endpoint)
        return fake.get(endpoint, params)
    with patch.object(client, '_api_get', side_effect=get):
        client._find_album('you made me / skin on linen', 'PISS')
        client._find_album('you made me / skin on linen', 'PISS')
    assert calls.count('artist') == 1
    client.reload_settings()
    with patch.object(client, '_api_get', side_effect=get):
        client._find_album('you made me / skin on linen', 'PISS')
    assert calls.count('artist') == 2


def test_wishlist_tracks_carry_the_release_group_id():
    from core.downloads.master import release_group_id_of
    from core.wishlist.service import WishlistService
    row = {'id': 91, 'spotify_track_id': 't1', 'failure_reason': None, 'retry_count': 0,
           'date_added': None, 'last_attempted': None, 'source_type': 'album',
           'source_info': {}, 'musicbrainz_release_group_id': 'rg-piss',
           'spotify_data': {'name': 'toni', 'artists': [{'name': 'PISS'}],
                            'album': {'name': 'you made me / skin on linen'}}}
    track = WishlistService.format_track_for_download(row)
    assert release_group_id_of([track]) == 'rg-piss'


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


def test_per_track_skips_lidarr_only_after_its_whole_album_turn():
    from core.downloads.album_bundle_dispatch import per_track_exclusions
    album = {'is_album_download': True}
    cfg = _cfg({'download_source.mode': 'hybrid'})
    # lidarr leads the chain: it had the album, per-track skips it
    assert per_track_exclusions(album, cfg, hybrid=True, bundle_sources=['lidarr']) == [
        'lidarr', 'torrent', 'usenet']
    # hifi, lidarr: no bundle turn, so per-track is how lidarr gets asked
    assert per_track_exclusions(album, cfg, hybrid=True, bundle_sources=[]) == ['torrent', 'usenet']
    # whole album off: lidarr skipped for every track, album or not
    off = _cfg({'download_source.mode': 'hybrid', 'lidarr_download.whole_album_default': False})
    assert per_track_exclusions({}, off, hybrid=True, bundle_sources=[]) == ['lidarr']
    # a playlist batch in hybrid with the switch on skips nothing
    assert per_track_exclusions({}, cfg, hybrid=True, bundle_sources=['lidarr']) is None
    # lidarr-only mode never skips lidarr
    assert per_track_exclusions(album, _cfg({'download_source.mode': 'lidarr'}),
                                hybrid=False, bundle_sources=['lidarr']) is None


# ── a grab that never moves, and handing the album on ─────────────────────────

def _queued(record_id, left, size=100, album='rg'):
    return {'id': record_id, 'album': {'foreignAlbumId': album}, 'status': 'queued',
            'size': size, 'sizeleft': left}


def test_wait_gives_up_on_a_grab_that_never_moves(client):
    """a torrent with no seeders sat in Lidarr's queue for the whole 6 h
    deadline, and only then did the batch move on to the next source."""
    client._stall_timeout = 1800

    def get(endpoint, params=None):
        if endpoint == 'queue':
            return {'records': [_queued(7, 100)]}
        return {'id': 100, 'statistics': {'trackFileCount': 0}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(100)):
        assert client._wait_for_album(100, 'rg') == 'stalled'


def test_a_magnet_that_never_got_metadata_is_stalled(client):
    """what really sat in the queue on 2026-11-21: status queued, size 0 and
    0 bytes left, because qBittorrent never found a peer for the metadata."""
    client._stall_timeout = 1800

    def get(endpoint, params=None):
        if endpoint == 'queue':
            return {'records': [_queued(7, 0, size=0)]}
        return {'id': 100, 'statistics': {'trackFileCount': 0}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(100)):
        assert client._wait_for_album(100, 'rg') == 'stalled'


def test_a_release_held_by_a_delay_profile_is_not_stalled(client):
    client._stall_timeout = 1800
    polls = {'n': 0}

    def get(endpoint, params=None):
        if endpoint == 'queue':
            polls['n'] += 1
            held = dict(_queued(7, 100), status='delay')
            return {'records': [] if polls['n'] > 30 else [held]}
        return {'id': 100, 'statistics': {'trackFileCount': 12 if polls['n'] > 30 else 0}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(600)):
        assert client._wait_for_album(100, 'rg') == 'complete'


def test_a_slow_download_is_not_stalled(client):
    client._stall_timeout = 1800
    polls = {'n': 0}

    def get(endpoint, params=None):
        if endpoint == 'queue':
            polls['n'] += 1
            if polls['n'] > 60:
                return {'records': []}
            return {'records': [_queued(7, 100 - polls['n'], size=100)]}
        return {'id': 100, 'statistics': {'trackFileCount': 12 if polls['n'] > 60 else 0}}

    # 5 hours, 5 minutes a poll: far past the stall timeout, never stalled
    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(300)):
        assert client._wait_for_album(100, 'rg') == 'complete'


def test_a_finished_download_waiting_on_its_import_is_not_stalled(client):
    client._stall_timeout = 1800
    polls = {'n': 0}

    def get(endpoint, params=None):
        if endpoint == 'queue':
            polls['n'] += 1
            done = dict(_queued(7, 0), status='completed', trackedDownloadState='importPending')
            return {'records': [] if polls['n'] > 30 else [done]}
        return {'id': 100, 'statistics': {'trackFileCount': 12 if polls['n'] > 30 else 0}}

    with patch.object(client, '_api_get', side_effect=get), \
            patch('core.lidarr_download_client.time.sleep'), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(600)):
        assert client._wait_for_album(100, 'rg') == 'complete'


def _hybrid(extra=None):
    cfg = {'download_source.mode': 'hybrid', 'download_source.hybrid_order': ['lidarr', 'soulseek']}
    cfg.update(extra or {})
    return patch('core.lidarr_download_client.config_manager.get',
                 side_effect=lambda k, d=None: cfg.get(k, d))


@pytest.mark.parametrize('owns_library', [False, True])
def test_a_stalled_album_is_handed_to_the_next_source(client, tmp_path, owns_library):
    """the stalled torrent stayed in Lidarr after the fallback, and the album
    stayed monitored: Lidarr delivered it as well, next to the fallback's copy."""
    client._stall_timeout = 1800
    client._owns_library = owns_library
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    fake.queue = [_queued(7, 100, album='rg-piss')]
    with _wired(client, fake), _hybrid(), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(100)), \
            patch('core.lidarr_download_client.http_requests.put') as put:
        out = client.download_album_to_staging('you made me / skin on linen', 'PISS',
                                               str(tmp_path / 's'))
    assert not out['success'] and out['fallback'] is True
    assert out['error'] == "Lidarr's download made no progress"
    assert fake.deleted == ['queue/7']
    unmonitored = [c for c in put.call_args_list if c.kwargs['json'] == {'albumIds': [100], 'monitored': False}]
    assert bool(unmonitored) is not owns_library


def test_a_miss_in_lidarr_only_mode_keeps_the_album(client, tmp_path):
    """nothing else will fetch it: Lidarr keeps the album and its grab."""
    client._stall_timeout = 1800
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    fake.queue = [_queued(7, 100, album='rg-piss')]
    with _wired(client, fake), _hybrid({'download_source.mode': 'lidarr'}), \
            patch('core.lidarr_download_client.time.monotonic', side_effect=_clock(100)), \
            patch('core.lidarr_download_client.http_requests.put') as put:
        out = client.download_album_to_staging('you made me / skin on linen', 'PISS',
                                               str(tmp_path / 's'))
    assert not out['success'] and out['fallback'] is False
    assert fake.deleted == []
    assert not [c for c in put.call_args_list if c.kwargs['json'].get('monitored') is False]


# ── Lidarr imports what other sources downloaded ──────────────────────────────

def _listed(path, rejections=(), track=900):
    return {'path': path, 'artist': {'id': 1}, 'album': {'id': 100}, 'albumReleaseId': 5,
            'tracks': [{'id': track}], 'quality': {'quality': {'name': 'FLAC'}},
            'indexerFlags': 0, 'rejections': [{'reason': r, 'type': 'permanent'} for r in rejections]}


def test_lidarr_imports_the_files_it_matches(client, tmp_path):
    files = _audio(tmp_path, 'PISS/album/01.flac', 'PISS/album/02.flac', 'PISS/album/03.flac')
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    fake.manual = [_listed(files[0]),
                   _listed(files[1], ['Not an upgrade for existing track file(s)']),
                   _listed(str(tmp_path / 'music/PISS/album/cover.flac'))]  # not ours
    with _wired(client, fake), patch('core.lidarr_download_client.http_requests.put'):
        out = client.import_into_library(files, 'you made me / skin on linen', 'PISS', monitored=True)
    assert out['imported'] == 1 and out['error'] is None
    assert out['paths'] == {files[0]: '/music/PISS/album/01.flac'}
    assert set(out['rejected']) == {files[1], files[2]}
    assert 'Not an upgrade' in out['rejected'][files[1]]
    assert out['rejected'][files[2]] == 'Lidarr did not list the file'
    command = fake.commands[-1]
    assert command['name'] == 'ManualImport' and command['importMode'] == 'copy'
    assert command['replaceExistingFiles'] is False
    assert [f['path'] for f in command['files']] == [files[0]]
    assert command['files'][0]['trackIds'] == [900] and command['files'][0]['albumReleaseId'] == 5
    assert fake.manual_folders == [str(tmp_path / 'music/PISS/album')]


def test_a_playlist_track_does_not_make_lidarr_fetch_its_album(client, tmp_path):
    files = _audio(tmp_path, 'PISS/album/01.flac')
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    fake.manual = [_listed(files[0])]
    with _wired(client, fake), patch('core.lidarr_download_client.http_requests.put') as put:
        out = client.import_into_library(files, 'you made me / skin on linen', 'PISS', monitored=False)
    assert out['imported'] == 1
    added = [d for e, d in fake.posts if e == 'album']
    assert added and added[0]['monitored'] is False
    assert not [c for c in fake.commands if c['name'] == 'AlbumSearch']
    assert not put.called


def test_an_album_lidarr_does_not_know_is_left_alone(client, tmp_path):
    files = _audio(tmp_path, 'X/album/01.flac')
    fake = FakeLidarr({})
    with _wired(client, fake):
        out = client.import_into_library(files, 'Nope', 'Nobody')
    assert out['imported'] == 0 and "Lidarr has no album" in out['error']
    assert not fake.commands and not fake.posts


def test_imports_never_lose_the_cleanup_that_would_forget_them(tmp_path):
    cfg = {'lidarr_download.import_through_lidarr': True, 'lidarr_download.cleanup_after_import': True}
    with patch('core.lidarr_download_client.config_manager.get',
               side_effect=lambda k, d=None: cfg.get(k, d)):
        c = LidarrDownloadClient(download_path=str(tmp_path / 'downloads'))
    assert c._owns_library is True and c._cleanup is False


def test_a_file_lidarr_turns_down_while_importing_is_not_reported_imported(client, tmp_path):
    """the command says completed either way: only the track files tell."""
    files = _audio(tmp_path, 'PISS/album/01.flac', 'PISS/album/02.flac')
    fake = FakeLidarr({'PISS you made me / skin on linen': [PISS]})
    fake.manual = [_listed(files[0], track=901), _listed(files[1], track=902)]
    fake.turned_down = {files[1]}
    fake.landed = [{'id': 1, 'dateAdded': '2026-11-01T10:00:00Z', 'path': '/music/PISS/album/old-02.flac'}]
    fake.track_files[902] = 1   # the track already had an older file
    with _wired(client, fake), patch('core.lidarr_download_client.http_requests.put'):
        out = client.import_files(files)
    assert out['paths'] == {files[0]: '/music/PISS/album/01.flac'}
    assert out['rejected'] == {files[1]: 'Lidarr did not import it (see History in Lidarr)'}
