"""#1573: the scan keeps both paths for a track.

Navidrome mounts the music folder at /music, SoulSync at its own folder. The
scan used to store Navidrome's path in file_path while downloads and reorganize
store SoulSync's, so one column held two forms and plain compares missed. Now
server_path holds what the server reported and file_path where SoulSync opens
the file. These run the real save, the real resolver and real files.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.settings as settings_module
from core.library import server_paths
from core.library.navidrome_identity import repair_rekeyed_tracks, resolve_tracks
from database.music_database import MusicDatabase

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


def _db(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('ar', 'The Killers', 'navidrome')")
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al', 'ar', 'Hot Fuss', 'navidrome')")
        conn.commit()
    return db


def _paths(db, track_id):
    with db._get_connection() as conn:
        row = conn.execute("SELECT file_path, server_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return row[0], row[1]


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

def test_scan_stores_both_paths(tmp_path, library):
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    assert _paths(db, 'nd-1') == (str(library / _REL), '/music/' + _REL)


def test_unreachable_file_keeps_todays_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, 'config_manager', _Config([]))
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    assert _paths(db, 'nd-1') == ('/music/' + _REL, '/music/' + _REL)


def test_rescan_never_swaps_a_working_local_path_for_the_servers(tmp_path, library, monkeypatch):
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    # this time nothing resolves (say the library path setting was cleared)
    monkeypatch.setattr(settings_module, 'config_manager', _Config([]))
    server_paths.reset()
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    assert _paths(db, 'nd-1') == (str(library / _REL), '/music/' + _REL)


def test_missing_server_path_keeps_both(tmp_path, library):
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    db.insert_or_update_media_track(_Track('nd-1', None), 'al', 'ar', server_source='navidrome')
    assert _paths(db, 'nd-1') == (str(library / _REL), '/music/' + _REL)


def test_export_hands_the_server_its_own_path(tmp_path, library):
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    assert [e['path'] for e in db.get_all_library_tracks_for_export()] == ['/music/' + _REL]


# -- navidrome re-keys ------------------------------------------------------

def _stale_and_live(tmp_path, library):
    """reorganize moved the file: navidrome gave it a new id and a new path,
    the scan brought the new row in. the old row still carries the old id."""
    db = _db(tmp_path)
    db.insert_or_update_media_track(_Track('dead', '/music/old/place.flac'), 'al', 'ar', server_source='navidrome')
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path = ? WHERE id = 'dead'", (str(library / _REL),))
        conn.commit()
    db.insert_or_update_media_track(_Track('live', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    songs = {'live': {'id': 'live', 'title': 'Everything Will Be Alright', 'duration': 345,
                      'path': '/music/' + _REL}}
    return db, songs


def test_playlist_guard_follows_a_rekey_through_the_local_file(tmp_path, library):
    db, songs = _stale_and_live(tmp_path, library)
    # the tail fallback would also find it; take it away to prove the new route
    songs['live']['path'] = '/music/elsewhere/x.flac'
    assert resolve_tracks([SimpleNamespace(ratingKey='dead')], songs, db)[0].ratingKey == 'live'


def test_repair_merges_a_rekeyed_row_through_the_local_file(tmp_path, library):
    db, songs = _stale_and_live(tmp_path, library)
    songs['live']['path'] = '/music/elsewhere/x.flac'
    assert repair_rekeyed_tracks(db, songs) == 1
    with db._get_connection() as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM tracks")] == ['live']
