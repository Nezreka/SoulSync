"""The Music Assistant client against a fake of the server's API.

The fake answers the commands the client sends with the shapes Music
Assistant 2.10.6 returns (library items with provider mappings, external
ids, images; playlist writes as background tasks). The tests pin: file
paths come from the Local files mapping joined to the source folder,
tracks without one are skipped, a failed page never reads as an empty
library, playlist writes wait for their task, and the wrappers satisfy
the shared item contract."""

from __future__ import annotations

import pytest

from core.media_server import items
from core.media_server.contract import Capability
from core.music_assistant_client import (
    MusicAssistantAlbum,
    MusicAssistantArtist,
    MusicAssistantClient,
    MusicAssistantError,
    MusicAssistantTrack,
)

SRC = 'filesystem_local--abc'


def _mapping(rel, instance=SRC, domain='filesystem_local', **fmt):
    return {'item_id': rel, 'provider_domain': domain, 'provider_instance': instance,
            'audio_format': {'content_type': 'flac', 'bit_rate': 900, 'sample_rate': 44100, 'bit_depth': 16, **fmt}}


ARTIST = {'item_id': 7, 'provider': 'library', 'name': 'Artist A', 'uri': 'library://artist/7',
          'external_ids': [['musicbrainz_artistid', 'mb-artist']],
          'metadata': {'description': 'bio', 'genres': ['rock'],
                       'images': [{'type': 'thumb', 'path': 'Artist A/folder.jpg', 'provider': SRC,
                                   'remotely_accessible': False}]},
          'date_added': '2026-01-02T03:04:05Z', 'provider_mappings': [{'item_id': 'Artist A', 'provider_domain': 'filesystem_local', 'provider_instance': SRC}]}
ALBUM = {'item_id': 70, 'provider': 'library', 'name': 'Album B', 'year': 2001, 'uri': 'library://album/70',
         'artists': [{'item_id': 7, 'name': 'Artist A'}],
         'external_ids': [['musicbrainz_albumid', 'mb-album']],
         'metadata': {'images': [{'type': 'thumb', 'path': 'https://cdn/x.jpg', 'provider': 'url', 'remotely_accessible': True}]},
         'date_added': '2026-01-02T03:04:05Z'}
TRACK = {'item_id': 700, 'provider': 'library', 'name': 'Song C', 'uri': 'library://track/700', 'duration': 61,
         'track_number': 3, 'disc_number': 2, 'album': {'item_id': 70, 'name': 'Album B', 'year': 2001},
         'artists': [{'item_id': 7, 'name': 'Artist A'}, {'item_id': 8, 'name': 'Artist D'}],
         'external_ids': [['musicbrainz_recordingid', 'mb-rec'], ['isrc', 'US123']],
         'provider_mappings': [_mapping('Artist A/Album B/03 Song C.flac')],
         'date_added': '2026-01-02T03:04:05Z'}
STREAM_ONLY = {'item_id': 701, 'provider': 'library', 'name': 'Stream only', 'duration': 10,
               'album': {'item_id': 70}, 'artists': [], 'external_ids': [],
               'provider_mappings': [{'item_id': 'sp1', 'provider_domain': 'spotify', 'provider_instance': 'spotify--x'}]}
CUE = {'item_id': 702, 'provider': 'library', 'name': 'Cue part', 'duration': 10, 'album': {'item_id': 70},
       'artists': [], 'external_ids': [], 'provider_mappings': [_mapping('Artist A/Album B/all.flac::2')]}


class Fake:
    """Records every command; answers from a script keyed by command."""

    def __init__(self):
        self.calls = []
        self.tasks = {}
        self.playlists = {1: {'item_id': 1, 'name': 'Mix', 'is_editable': True, 'owner': 'rob', 'metadata': {},
                              'tracks': [TRACK]}}
        self.next_id = 2

    def __call__(self, command, args):
        self.calls.append((command, args))
        if command == 'info':
            return {'server_version': '2.10.6', 'schema_version': 65}
        if command == 'auth/me':
            return {'username': 'rob'}
        if command == 'tasks/list':
            return [{'task_id': 't-sync', 'name': 'Sync filesystem_local', 'status': 'running'}]
        if command == 'providers':
            return [{'instance_id': SRC, 'domain': 'filesystem_local', 'name': 'Local files'},
                    {'instance_id': 'spotify--x', 'domain': 'spotify', 'name': 'Spotify'}]
        if command == 'config/providers/get_value':
            assert args == {'instance_id': SRC, 'key': 'path'}
            return '/media/music/'
        if command == 'music/artists/library_items':
            return [ARTIST] if args.get('offset', 0) == 0 else []
        if command == 'music/albums/library_items':
            return [ALBUM] if args.get('offset', 0) == 0 else []
        if command == 'music/artists/artist_albums':
            return [ALBUM, {**ALBUM, 'item_id': 'sp9', 'provider': 'spotify--x'}]
        if command == 'music/albums/album_tracks':
            return [TRACK, STREAM_ONLY, CUE]
        if command == 'music/artists/get':
            return ARTIST if str(args['item_id']) == '7' else None
        if command == 'music/albums/get':
            return ALBUM if str(args['item_id']) == '70' else None
        if command.endswith('/count'):
            return 3
        if command == 'music/sync':
            return [{'task_id': 't-sync', 'status': 'pending'}]
        if command == 'music/search':
            return {'tracks': [TRACK]}
        if command == 'music/playlists/library_items':
            return list(self.playlists.values()) if args.get('offset', 0) == 0 else []
        if command == 'music/playlists/get':
            return self.playlists.get(int(args['item_id']))
        if command == 'music/playlists/playlist_tracks':
            return [{**t, 'position': i} for i, t in enumerate(self.playlists[int(args['item_id'])]['tracks'])]
        if command == 'music/playlists/create_playlist':
            pid = self.next_id
            self.next_id += 1
            self.playlists[pid] = {'item_id': pid, 'name': args['name'], 'is_editable': True, 'owner': 'rob',
                                   'metadata': {}, 'tracks': []}
            return self.playlists[pid]
        if command == 'music/playlists/add_playlist_tracks':
            pl = self.playlists[int(args['db_playlist_id'])]
            for uri in args['uris']:
                tid = int(uri.rsplit('/', 1)[-1])
                pl['tracks'].append({**TRACK, 'item_id': tid, 'uri': uri})
            return self._task('add')
        if command == 'music/playlists/remove_playlist_tracks':
            pl = self.playlists[int(args['db_playlist_id'])]
            drop = set(args['positions_to_remove'])
            pl['tracks'] = [t for i, t in enumerate(pl['tracks']) if i not in drop]
            return self._task('remove')
        if command == 'music/playlists/update':
            self.playlists[int(args['item_id'])]['name'] = args['update']['name']
            return self.playlists[int(args['item_id'])]
        if command == 'music/playlists/remove':
            self.playlists.pop(int(args['item_id']), None)
            return None
        if command == 'tasks/get':
            task = self.tasks[args['task_id']]
            task['polls'] = task.get('polls', 0) + 1
            task['status'] = 'finished' if task['polls'] >= 2 else 'running'
            return task
        raise MusicAssistantError(f"fake: no answer for {command}")

    def _task(self, kind):
        tid = f"t-{kind}-{len(self.tasks)}"
        self.tasks[tid] = {'task_id': tid, 'status': 'pending'}
        return dict(self.tasks[tid])


@pytest.fixture
def client(monkeypatch):
    fake = Fake()
    monkeypatch.setattr('core.music_assistant_client.config_manager.get',
                        lambda key, default=None: {'base_url': 'http://ma:8095', 'token': 'tok', 'source_instance': SRC}
                        if key == 'music_assistant' else default)
    monkeypatch.setattr('core.music_assistant_client.time.sleep', lambda s: None)
    c = MusicAssistantClient(transport=fake)
    c.fake = fake
    return c


def test_declares_the_mvp_capabilities_honestly():
    caps = MusicAssistantClient.CAPABILITIES
    assert {Capability.SEARCH, Capability.RECENTLY_ADDED, Capability.SCAN_TRIGGER,
            Capability.LIBRARY_STATS, Capability.PLAYLISTS} <= caps
    assert Capability.PLAYLIST_IMAGE not in caps and Capability.TAGS_AUTODETECTED not in caps
    assert MusicAssistantClient.declared_capability_gaps() == {}


def test_connection_reads_version_and_user(client):
    assert client.ensure_connection() is True
    assert client.is_connected() and client.server_version == '2.10.6' and client.user_name == 'rob'
    ok, message = client.test_connection()
    assert ok and '2.10.6' in message and '/media/music' in message


def test_no_token_means_not_connected(monkeypatch):
    monkeypatch.setattr('core.music_assistant_client.config_manager.get',
                        lambda key, default=None: {'base_url': 'http://ma:8095'} if key == 'music_assistant' else default)
    c = MusicAssistantClient(transport=Fake())
    assert c.ensure_connection() is False and c.is_connected() is False


def test_scan_reads_follow_the_item_contract(client):
    artists = client.get_all_artists()
    assert client.last_fetch_failed is False
    assert len(artists) == 1 and isinstance(artists[0], items.MediaArtist)
    artist = artists[0]
    assert artist.ratingKey == '7' and artist.title == 'Artist A' and artist.summary == 'bio'
    assert artist.genres == ['rock'] and artist.musicBrainzId == 'mb-artist'
    assert artist.thumb == f"ma:{SRC}:Artist A/folder.jpg"
    albums = artist.albums()
    assert [a.ratingKey for a in albums] == ['70'], 'only library albums, not the provider copies'
    album = albums[0]
    assert isinstance(album, MusicAssistantAlbum) and album.year == 2001 and album.musicBrainzId == 'mb-album'
    assert album.thumb == 'https://cdn/x.jpg'
    for attr in items.ALBUM_ATTRS:
        assert hasattr(album, attr), attr
    tracks = album.tracks()
    assert [t.ratingKey for t in tracks] == ['700'], 'stream-only and cue sub-tracks have no path and are skipped'
    track = tracks[0]
    assert isinstance(track, MusicAssistantTrack)
    for attr in items.TRACK_ATTRS:
        assert hasattr(track, attr), attr
    assert track.path == '/media/music/Artist A/Album B/03 Song C.flac'
    assert track.duration == 61000 and track.bitRate == 900 and track.suffix == 'flac'
    assert track.trackNumber == 3 and track.discNumber == 2 and track.year == 2001
    assert track.musicBrainzId == 'mb-rec' and track.isrc == 'US123'
    assert track._artist_ids == ['7', '8'] and track._album_id == '70'
    assert track.artist().title == 'Artist A' and track.album().title == 'Album B'
    assert client.get_all_album_ids() == {'70'} and client.get_all_artist_ids() == {'7'}


def test_a_failed_page_never_reads_as_an_empty_library(client):
    calls = []

    def broken(command, args):
        calls.append(command)
        if command == 'music/artists/library_items':
            raise MusicAssistantError('boom')
        return client.fake(command, args)

    client._transport = broken
    assert client.get_all_artists() == []
    assert client.last_fetch_failed is True


def test_listing_pages_until_a_short_page(client):
    big = [{**ARTIST, 'item_id': i, 'name': f'A{i}'} for i in range(500)]

    def paged(command, args):
        if command == 'music/artists/library_items':
            return big if args['offset'] == 0 else [ARTIST]
        return client.fake(command, args)

    client._transport = paged
    assert len(client.get_all_artists()) == 501


def test_recently_added_stats_scan_and_search(client):
    recent = client.get_recently_added_albums(limit=10)
    assert [a.ratingKey for a in recent] == ['70']
    cmd, args = [c for c in client.fake.calls if c[0] == 'music/albums/library_items'][-1]
    assert args['order_by'] == 'timestamp_added_desc' and args['provider'] == SRC
    assert client.get_library_stats() == {'artists': 3, 'albums': 3, 'tracks': 3, 'playlists': 3}
    assert client.trigger_library_scan() is True
    assert [c for c in client.fake.calls if c[0] == 'music/sync'][-1][1] == {'providers': [SRC]}
    assert client.is_library_scanning() is True
    hits = client.search_tracks('Song C', 'Artist A')
    assert len(hits) == 1 and hits[0].id == '700' and hits[0].artist == 'Artist A' and hits[0].duration == 61000
    assert [c for c in client.fake.calls if c[0] == 'music/search'][-1][1]['library_only'] is True


def test_playlist_round_trip_waits_for_each_task(client):
    class T:
        def __init__(self, key):
            self.ratingKey = key

    new_id = client.create_playlist('Fresh', [T('700'), T('701')])
    assert new_id == '2'
    assert client.fake.playlists[2]['name'] == 'Fresh'
    assert [t['item_id'] for t in client.fake.playlists[2]['tracks']] == [700, 701]
    polls = [c for c in client.fake.calls if c[0] == 'tasks/get']
    assert polls, 'the add was awaited through tasks/get'

    assert client.get_playlist_by_name('fresh').id == '2'
    assert client.get_playlist_track_ids('2') == ['700', '701']

    # append skips what is already there
    assert client.append_to_playlist('Fresh', [T('701'), T('702')]) is True
    assert [t['item_id'] for t in client.fake.playlists[2]['tracks']] == [700, 701, 702]

    # reconcile removes the gone and adds the missing, by position
    assert client.reconcile_playlist('Fresh', [T('700'), T('703')], playlist_id='2') is True
    assert [t['item_id'] for t in client.fake.playlists[2]['tracks']] == [700, 703]

    # update replaces in order
    assert client.update_playlist('Fresh', [T('703'), T('700')], playlist_id='2') is True
    assert [t['item_id'] for t in client.fake.playlists[2]['tracks']] == [703, 700]

    assert client.rewrite_playlist_order('2', 'Fresh', ['700', '703']) is True
    assert [t['item_id'] for t in client.fake.playlists[2]['tracks']] == [700, 703]

    assert client.copy_playlist('Fresh', 'Fresh copy') is True
    assert [t['item_id'] for t in client.fake.playlists[3]['tracks']] == [700, 703]
    assert client.delete_playlist('3') is True and 3 not in client.fake.playlists
    assert client.set_playlist_image('Fresh', 'https://x/y.jpg') is False


def test_a_failed_task_fails_the_write(client):
    class T:
        ratingKey = '700'

    def failing(command, args):
        if command == 'tasks/get':
            return {'task_id': args['task_id'], 'status': 'failed', 'error': 'disk full'}
        return client.fake(command, args)

    client._transport = failing
    assert client.create_playlist('Doomed', [T()]) is False


def test_image_url_builds_the_proxy_or_passes_a_remote_url(client):
    assert client.image_url(f"ma:{SRC}:Artist A/folder.jpg") == (
        f"http://ma:8095/imageproxy?path=Artist%20A%2Ffolder.jpg&provider={SRC.replace('--', '--')}&size=0")
    assert client.image_url('https://cdn/x.jpg') == 'https://cdn/x.jpg'
    assert client.image_url('/library/metadata/1/thumb') is None
    assert client.image_url(None) is None


def test_sources_and_source_folder(client):
    assert client.get_sources() == [{'key': SRC, 'title': 'Local files', 'domain': 'filesystem_local'}]
    assert client.source_folder() == '/media/music'
    assert client.resolve_path({'item_id': 'a/b.flac', 'provider_domain': 'filesystem_local', 'provider_instance': SRC}) == '/media/music/a/b.flac'
    assert client.resolve_path({'item_id': 'x', 'provider_domain': 'spotify', 'provider_instance': 'spotify--x'}) is None
    assert client.resolve_path({'item_id': 'a/all.flac::3', 'provider_domain': 'filesystem_local', 'provider_instance': SRC}) is None


def test_descriptor_and_registry_know_the_server():
    from core.media_server import specs
    from core.media_server.registry import build_default_registry

    d = specs.describe('music_assistant')
    assert d is not None and d.is_external and d.library_concept == 'source'
    assert d.required_keys == ('base_url', 'token') and d.secret_keys == ('token',)
    assert d.detection.port == 8095
    assert specs.describe('ma') is d
    assert 'music_assistant' in build_default_registry().names()
    assert 'music_assistant' in specs.external_names()


def test_scan_hints_and_wrappers_report_the_server(client):
    assert client.scan_hints()['caches_listings'] is True
    assert MusicAssistantArtist(ARTIST, client).server == 'music_assistant'
    client.get_all_artists()
    assert client.get_cache_stats()['artists_cached'] == 1
    client.clear_cache()
    assert client.get_cache_stats()['artists_cached'] == 0
