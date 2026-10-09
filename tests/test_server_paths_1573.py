"""#1573: the scan keeps both paths for a track.

Navidrome mounts the music folder at /music, SoulSync at its own folder. The
scan used to store Navidrome's path in file_path while downloads and reorganize
store SoulSync's, so one column held two forms and plain compares missed. Now
server_path holds what the server reported and file_path where SoulSync opens
the file. These run the real save, the real resolver and real files.
"""
from pathlib import Path

import pytest

import core.settings as settings_module
from core.library import server_paths

_REL = 'The Killers/The Killers - Hot Fuss/11 - Everything Will Be Alright.flac'


class _Config:
    def __init__(self, music_paths):
        self.music_paths = music_paths

    def get(self, key, default=None):
        if key == 'library.music_paths':
            return self.music_paths
        return default


class _Track:
    def __init__(self, track_id, path, title='Everything Will Be Alright'):
        self.ratingKey = track_id
        self.title = title
        self.trackNumber = 11
        self.duration = 345000
        self.path = path
        self.bitRate = 1000


@pytest.fixture(autouse=True)
def _fresh_mounts():
    server_paths.reset()
    yield
    server_paths.reset()


@pytest.fixture
def library(tmp_path, monkeypatch):
    """a real file under SoulSync's mount; the server calls the mount /music"""
    root = tmp_path / 'Media' / 'Music'
    song = root / _REL
    song.parent.mkdir(parents=True)
    song.write_bytes(b'audio')
    monkeypatch.setattr(settings_module, 'config_manager', _Config([str(root)]))
    return root


# -- the translator ---------------------------------------------------------

def test_a_path_that_exists_here_is_its_own_local_path(library):
    assert server_paths.local_path_for(str(library / _REL)) == str(library / _REL)


def test_resolves_another_mount_and_learns_it(library):
    assert server_paths.local_path_for('/music/' + _REL, settings_module.config_manager) == str(library / _REL)
    assert server_paths._learned == {'/music': str(library).replace('\\', '/')}


def test_learned_mount_skips_the_walk(library, monkeypatch):
    server_paths.local_path_for('/music/' + _REL, settings_module.config_manager)
    other = library / 'The Killers' / 'The Killers - Hot Fuss' / '01 - Jenny.flac'
    other.write_bytes(b'audio')
    import core.library.path_resolver as resolver
    monkeypatch.setattr(resolver, 'resolve_library_file_path',
                        lambda *a, **k: pytest.fail('learned mount should not walk'))
    assert server_paths.local_path_for('/music/The Killers/The Killers - Hot Fuss/01 - Jenny.flac') == str(other)


def test_unreachable_gives_none_and_stops_walking(tmp_path, monkeypatch):
    calls = []
    import core.library.path_resolver as resolver
    monkeypatch.setattr(resolver, 'resolve_library_file_path', lambda *a, **k: calls.append(1))
    for i in range(server_paths._GIVE_UP_AFTER + 50):
        assert server_paths.local_path_for(f'/music/A/B/{i}.flac') is None
    assert len(calls) == server_paths._GIVE_UP_AFTER



def test_a_hit_resets_the_miss_count(library, monkeypatch):
    """a partly mounted library keeps walking as long as hits interleave"""
    import core.library.path_resolver as resolver
    real = resolver.resolve_library_file_path
    calls = []

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(resolver, 'resolve_library_file_path', counting)
    for i in range(server_paths._GIVE_UP_AFTER - 1):
        server_paths.local_path_for(f'/elsewhere/A/B/{i}.flac', settings_module.config_manager)
    server_paths.local_path_for('/music/' + _REL, settings_module.config_manager)
    for i in range(10):
        server_paths.local_path_for(f'/elsewhere/A/C/{i}.flac', settings_module.config_manager)
    assert len(calls) == server_paths._GIVE_UP_AFTER + 10


# -- the scan save ----------------------------------------------------------

# The rest of upstream's #1573 tests pin the legacy tracks table: the scan
# storing server_path beside file_path, the M3U export and the Navidrome
# re-key through the local file. Library v2's media-server scan only maps
# server ids onto catalogue tracks (no server path is stored), and the re-key
# candidates run on lib2_media_server_mappings (tests/test_navidrome_identity.py).
