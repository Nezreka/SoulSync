"""lidarr_download.import_through_lidarr: where downloads land, what is handed
to Lidarr when a batch finishes, and what the transfer folder keeps."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

from core.downloads.lidarr_handoff import (
    files_for_lidarr, finish_imported, hand_batch_to_lidarr, sweep_lidarr_imports, waiting_files)
from core.imports import paths


def _file(root: Path, rel: str, age: float = 0) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'audio')
    if age:
        old = time.time() - age
        os.utime(p, (old, old))
    return str(p)


def _task(path, username='peer', status='completed', album='Album', artist='Artist', **info):
    return {'status': status, 'username': username, 'final_file_path': path,
            'track_info': {'name': 'Song', 'artists': [{'name': artist}],
                           'album': {'name': album, 'release_date': '2026-01-01'}, **info}}


def _in_library(path):
    return '/music/' + '/'.join(Path(path).parts[-3:])


class FakeClient:
    """Lidarr takes every file except those in ``refuse``."""

    def __init__(self, refuse=()):
        self.calls = []
        self.swept = []
        self.refuse = set(refuse)

    def _outcome(self, files):
        took = {p: _in_library(p) for p in files if p not in self.refuse}
        return {'imported': len(took), 'paths': took, 'error': None,
                'rejected': {p: 'Not an upgrade' for p in files if p in self.refuse}}

    def import_into_library(self, files, album, artist, **kwargs):
        self.calls.append((sorted(files), album, artist, kwargs))
        return self._outcome(files)

    def import_files(self, files, artist_id=None, result=None):
        self.swept.append(sorted(files))
        return self._outcome(files)


def test_an_album_batch_goes_to_lidarr_as_one_monitored_album(tmp_path):
    root = tmp_path / 'imports'
    a = _file(root, 'Artist/Album/01.flac')
    b = _file(root, 'Artist/Album/02.flac')
    outside = _file(tmp_path / 'elsewhere', '03.flac')
    batch = {'is_album_download': True, 'album_context': {'name': 'Album'},
             'artist_context': {'name': 'Artist'}}
    tasks = {
        1: _task(a, musicbrainz_release_group_id='rg-1'),
        2: _task(b, musicbrainz_release_group_id='rg-1'),
        3: _task(_file(root, 'Artist/Album/04.flac'), username='lidarr'),  # Lidarr has it
        4: _task(_file(root, 'Artist/Album/05.flac'), status='failed'),
        5: _task(outside),                                                  # not in the folder
    }
    client = FakeClient()
    moved = []
    n = hand_batch_to_lidarr('b1', batch, tasks, client=client, enabled=lambda: True,
                             transfer_root=str(root), repoint=lambda o, n: moved.append((o, n)))
    assert n == 2
    assert client.calls == [([a, b], 'Album', 'Artist',
                             {'release_group_id': 'rg-1', 'release_year': '2026',
                              'release_type': '', 'monitored': True})]
    assert moved == [(a, _in_library(a)), (b, _in_library(b))]
    assert not os.path.exists(a) and not os.path.exists(b)
    assert os.path.exists(outside)


def test_a_playlist_batch_goes_by_each_tracks_album_unmonitored(tmp_path):
    root = tmp_path / 'imports'
    a = _file(root, 'A/X/01.flac')
    b = _file(root, 'B/Y/01.flac')
    tasks = {1: _task(a, album='X', artist='A'), 2: _task(b, album='Y', artist='B')}
    groups = files_for_lidarr({'is_album_download': False}, tasks, str(root))
    assert sorted(groups) == [('X', 'A'), ('Y', 'B')]
    client = FakeClient()
    hand_batch_to_lidarr('b2', {}, tasks, client=client, enabled=lambda: True,
                         transfer_root=str(root), repoint=lambda o, n: None)
    assert {c[3]['monitored'] for c in client.calls} == {False}


def test_a_lidarr_album_bundle_is_not_handed_back(tmp_path):
    root = tmp_path / 'imports'
    tasks = {1: _task(_file(root, 'A/X/01.flac'))}
    assert files_for_lidarr({'album_bundle_source': 'lidarr'}, tasks, str(root)) == {}


def test_nothing_is_handed_over_with_the_setting_off(tmp_path):
    root = tmp_path / 'imports'
    client = FakeClient()
    tasks = {1: _task(_file(root, 'A/X/01.flac'))}
    assert hand_batch_to_lidarr('b', {}, tasks, client=client, enabled=lambda: False,
                                transfer_root=str(root)) == 0
    assert client.calls == [] and client.swept == []


def test_an_imported_copy_goes_with_its_lyrics_and_empty_folder(tmp_path):
    root = tmp_path / 'imports'
    song = _file(root, 'A/X/01 - Song.flac')
    lyrics = _file(root, 'A/X/01 - Song.lrc')
    cover = _file(root, 'A/X/cover.jpg')
    kept = _file(root, 'A/Y/01.flac')
    assert finish_imported({song: '/music/A/X/01.flac'}, str(root), repoint=lambda o, n: 1) == 1
    assert not any(os.path.exists(p) for p in (song, lyrics, cover))
    assert not (root / 'A' / 'X').exists()
    assert os.path.exists(kept) and (root / 'A').is_dir()


def test_a_copy_whose_rows_could_not_follow_is_kept(tmp_path):
    root = tmp_path / 'imports'
    song = _file(root, 'A/X/01.flac')

    def broken(old, new):
        raise RuntimeError('database locked')

    assert finish_imported({song: '/music/A/X/01.flac'}, str(root), repoint=broken) == 0
    assert os.path.exists(song)


def test_nothing_outside_the_transfer_folder_is_deleted(tmp_path):
    elsewhere = _file(tmp_path / 'music', 'A/X/01.flac')
    finish_imported({elsewhere: '/music/A/X/01.flac'}, str(tmp_path / 'imports'),
                    repoint=lambda o, n: 1)
    assert os.path.exists(elsewhere)


def test_the_sweep_hands_over_old_leftovers_and_keeps_what_lidarr_refuses(tmp_path):
    """a restart mid-batch used to strand its files for good."""
    root = tmp_path / 'imports'
    old = _file(root, 'A/X/01.flac', age=7 * 3600)
    refused = _file(root, 'A/X/02.flac', age=7 * 3600)
    young = _file(root, 'B/Y/01.flac')           # a batch may still be running
    sidecar = _file(root, 'A/X/03.lrc', age=7 * 3600)
    assert waiting_files(str(root)) == [old, refused]
    client = FakeClient(refuse={refused})
    taken = sweep_lidarr_imports(client=client, enabled=lambda: True,
                                 transfer_root=str(root), repoint=lambda o, n: 1)
    assert taken == 1
    assert client.swept == [[old, refused]]
    assert not os.path.exists(old)
    assert os.path.exists(refused) and os.path.exists(young) and os.path.exists(sidecar)


def _settings(values):
    class Cfg:
        def get(self, key, default=None):
            return values.get(key, default)
    return patch.object(paths, '_get_config_manager', return_value=Cfg())


def test_every_source_lands_in_the_lidarr_folder_when_lidarr_imports_everything(tmp_path):
    soulseek = {'original_search_result': {'username': 'some-peer'}}
    folder = str(tmp_path / 'lidarr-imports')
    with _settings({'lidarr_download.transfer_path': folder,
                    'lidarr_download.import_through_lidarr': True}), \
            patch.object(paths, 'config_root_path', side_effect=lambda p, *_: p):
        assert paths.lidarr_root_for_context(soulseek) == folder
    with _settings({'lidarr_download.transfer_path': folder}), \
            patch.object(paths, 'config_root_path', side_effect=lambda p, *_: p):
        assert paths.lidarr_root_for_context(soulseek) is None


def test_the_setting_needs_a_folder_lidarr_can_read():
    with _settings({'lidarr_download.import_through_lidarr': True}):
        assert paths.imports_through_lidarr() is False
        assert paths.lidarr_root_for_context({'search_result': {'username': 'peer'}}) is None


def _tags(by_name):
    def read(path):
        return {'available': True, 'tags': {'musicbrainz_releasegroupid': by_name[os.path.basename(path)]}}
    return patch('core.library.file_tags.read_embedded_tags', side_effect=read)


def test_the_files_tags_name_the_album_when_the_tracks_do_not(tmp_path):
    """a Deezer album carries no MusicBrainz id, and Lidarr's text search did
    not find 'XXXXX' by Arca: the files Lidarr was offered stayed out of the
    library. The consistency pass had tagged them with the release group."""
    root = tmp_path / 'imports'
    a = _file(root, 'Arca/XXXXX/01.flac')
    b = _file(root, 'Arca/XXXXX/02.flac')
    batch = {'is_album_download': True, 'album_context': {'name': 'XXXXX'},
             'artist_context': {'name': 'Arca'}}
    client = FakeClient()
    with _tags({'01.flac': 'rg-xxxxx', '02.flac': 'rg-xxxxx'}):
        hand_batch_to_lidarr('b', batch, {1: _task(a), 2: _task(b)}, client=client,
                             enabled=lambda: True, transfer_root=str(root), repoint=lambda o, n: 1)
    assert client.calls[0][3]['release_group_id'] == 'rg-xxxxx'


def test_files_that_disagree_name_no_album(tmp_path):
    from core.downloads.lidarr_handoff import tagged_release_group
    root = tmp_path / 'imports'
    files = [_file(root, 'A/X/01.flac'), _file(root, 'A/X/02.flac')]
    with _tags({'01.flac': 'rg-1', '02.flac': 'rg-2'}):
        assert tagged_release_group(files) is None


def test_the_sweep_names_the_album_from_the_tags_and_never_monitors_it(tmp_path):
    root = tmp_path / 'imports'
    a = _file(root, 'Arca/XXXXX/01.flac', age=7 * 3600)

    def read(path):
        return {'available': True, 'tags': {'album': 'XXXXX', 'albumartist': 'Arca',
                                            'musicbrainz_releasegroupid': 'rg-xxxxx'}}

    client = FakeClient()
    with patch('core.library.file_tags.read_embedded_tags', side_effect=read):
        taken = sweep_lidarr_imports(client=client, enabled=lambda: True,
                                     transfer_root=str(root), repoint=lambda o, n: 1)
    assert taken == 1 and client.swept == []
    assert client.calls == [([a], 'XXXXX', 'Arca',
                             {'release_group_id': 'rg-xxxxx', 'monitored': False})]
