"""plex reports a folder in a different case than the one on disk.

SeadogsBooty: plex says ``N:\\...\\Bring Me the Horizon\\...`` while the folder
here is ``Bring Me The Horizon``. on a case-sensitive mount the resolver's exact
checks never match, so the scan kept the plex path in file_path and hundreds of
real tracks looked missing. one unambiguous match ignoring case is the file;
two candidates are still a refusal. real files, real scan save.
"""
import os

import pytest

import core.settings as settings_module
from core.library import server_paths
from core.library.path_resolver import resolve_library_file_path
from database.music_database import MusicDatabase

_PLEX = r'N:\Libraries\Music\mp3\Bring Me the Horizon\Album\[2008] Suicide Season\01 - The Comedown.mp3'
_DISK = os.path.join('Bring Me The Horizon', 'Album', '[2008] Suicide Season', '01 - The Comedown.mp3')


class _Config:
    def __init__(self, music_paths):
        self.music_paths = music_paths

    def get(self, key, default=None):
        if key == 'library.music_paths':
            return self.music_paths
        return default


class _Track:
    def __init__(self, track_id, path):
        self.ratingKey = track_id
        self.title = 'The Comedown'
        self.trackNumber = 1
        self.duration = 200000
        self.path = path
        self.bitRate = 320


@pytest.fixture(autouse=True)
def _fresh_mounts():
    server_paths.reset()
    yield
    server_paths.reset()


@pytest.fixture
def music(tmp_path, monkeypatch):
    root = tmp_path / 'music'
    song = root / _DISK
    song.parent.mkdir(parents=True)
    song.write_bytes(b'audio')
    if os.path.exists(str(root / 'bring me the horizon')):
        pytest.skip('case-insensitive filesystem: exact checks already match')
    cfg = _Config([str(root)])
    monkeypatch.setattr(settings_module, 'config_manager', cfg)
    return root, cfg


def test_scan_stores_the_real_path_and_keeps_plex_in_server_path(tmp_path, music):
    root, _ = music
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('ar', 'Bring Me The Horizon', 'plex')")
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al', 'ar', 'Suicide Season', 'plex')")
        conn.commit()
    db.insert_or_update_media_track(_Track('px-1', _PLEX), 'al', 'ar', server_source='plex')
    with db._get_connection() as conn:
        row = conn.execute("SELECT file_path, server_path FROM tracks WHERE id = 'px-1'").fetchone()
    assert (row[0], row[1]) == (str(root / _DISK), _PLEX)


def test_exact_casing_still_wins(music):
    root, cfg = music
    exact = root / 'Bring Me the Horizon' / 'Album' / '[2008] Suicide Season' / '01 - The Comedown.mp3'
    exact.parent.mkdir(parents=True)
    exact.write_bytes(b'other copy')
    assert resolve_library_file_path(_PLEX, config_manager=cfg) == str(exact)


def test_two_folders_differing_only_by_case_refuse(music):
    root, cfg = music
    split = root / 'BRING ME THE HORIZON' / 'Album' / '[2008] Suicide Season' / '01 - The Comedown.mp3'
    split.parent.mkdir(parents=True)
    split.write_bytes(b'audio')
    assert resolve_library_file_path(_PLEX, config_manager=cfg) is None


def test_two_files_differing_only_by_case_refuse(music):
    root, cfg = music
    plex = r'N:\mp3\Bring Me the Horizon\Album\[2008] Suicide Season\01 - the comedown.mp3'
    (root / _DISK).with_name('01 - THE COMEDOWN.mp3').write_bytes(b'audio')
    assert resolve_library_file_path(plex, config_manager=cfg) is None


def test_a_file_casing_difference_alone_resolves(music):
    root, cfg = music
    plex = r'N:\mp3\Bring Me The Horizon\Album\[2008] Suicide Season\01 - the comedown.MP3'
    assert resolve_library_file_path(plex, config_manager=cfg) == str(root / _DISK)


def test_unknown_artist_is_still_a_miss(music):
    _, cfg = music
    plex = r'N:\mp3\Someone Else\Album\[2008] Suicide Season\01 - The Comedown.mp3'
    assert resolve_library_file_path(plex, config_manager=cfg) is None
