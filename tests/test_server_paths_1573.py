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

# The re-key behavior is exercised in tests/test_navidrome_identity.py.
# Below, the scan/export behavior uses the native catalogue and mappings.

# The server's observed path belongs to its mapping, never to catalogue ownership.
def _db(tmp_path, library, server='navidrome'):
    from database.music_database import MusicDatabase
    from tests.lib2_seed import track
    from core.library2.media_mappings import upsert_mapping
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        tid = track(conn, 'The Killers', 'Hot Fuss', 'Everything Will Be Alright',
                    path=str(library / _REL), track_number=11, duration=345000)
        row = conn.execute('SELECT t.album_id, al.primary_artist_id FROM lib2_tracks t '
                           'JOIN lib2_albums al ON al.id=t.album_id WHERE t.id=?', (tid,)).fetchone()
        upsert_mapping(conn, 'artist', row[1], server, 'ar')
        upsert_mapping(conn, 'album', row[0], server, 'al')
        conn.commit()
    return db, tid


def _paths(db, tid, server='navidrome'):
    with db._get_connection() as conn:
        return tuple(conn.execute('SELECT f.path, m.server_path FROM lib2_track_files f '
                                  'JOIN lib2_media_server_mappings m ON m.entity_id=f.track_id '
                                  "AND m.entity_type='track' AND m.server_source=? "
                                  'WHERE f.track_id=?', (server, tid)).fetchone())


def test_scan_keeps_server_path_on_mapping_and_local_file_on_catalogue(tmp_path, library):
    db, tid = _db(tmp_path, library)
    assert db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    assert _paths(db, tid) == (str(library / _REL), '/music/' + _REL)


def test_unresolved_server_mount_preserves_the_imported_local_path(tmp_path, library, monkeypatch):
    db, tid = _db(tmp_path, library)
    monkeypatch.setattr(settings_module, 'config_manager', _Config([]))
    assert db.insert_or_update_media_track(_Track('nd-1', '/different-mount/' + _REL), 'al', 'ar', server_source='navidrome')
    assert _paths(db, tid) == (str(library / _REL), '/different-mount/' + _REL)


def test_missing_server_path_keeps_the_last_observation(tmp_path, library):
    db, tid = _db(tmp_path, library)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    db.insert_or_update_media_track(_Track('nd-1', None), 'al', 'ar', server_source='navidrome')
    assert _paths(db, tid) == (str(library / _REL), '/music/' + _REL)


def test_export_uses_the_selected_servers_own_mount(tmp_path, library):
    from core.library2.media_mappings import upsert_mapping
    db, tid = _db(tmp_path, library)
    db.insert_or_update_media_track(_Track('nd-1', '/music/' + _REL), 'al', 'ar', server_source='navidrome')
    with db._get_connection() as conn:
        upsert_mapping(conn, 'track', tid, 'plex', 'plex-1', server_path='/plex/' + _REL)
        conn.commit()
    assert [e['path'] for e in db.get_all_library_tracks_for_export(server_source='navidrome')] == ['/music/' + _REL]
    assert [e['path'] for e in db.get_all_library_tracks_for_export(server_source='plex')] == ['/plex/' + _REL]
    assert [e['path'] for e in db.get_all_library_tracks_for_export(server_source='jellyfin')] == [str(library / _REL)]
    assert [e['file_path'] for e in db.get_tracks_for_m3u_resolution(server_source='navidrome')] == ['/music/' + _REL]
    assert _paths(db, tid)[0] == str(library / _REL)


@pytest.mark.parametrize('scope,expected', [('shared', '/shared/song.flac'), (7, '/private/song.flac')])
def test_export_selects_the_mapping_of_the_files_library(tmp_path, library, monkeypatch, scope, expected):
    from core.library2 import sql_util
    from core.library2.media_mappings import upsert_mapping
    db, tid = _db(tmp_path, library)
    with db._get_connection() as conn:
        conn.execute('INSERT INTO lib2_track_files(track_id,path,owner_profile_id,is_primary) '
                     'VALUES(?,?,7,0)', (tid, '/local/private/song.flac'))
        upsert_mapping(conn, 'track', tid, 'navidrome', 'shared-song', server_path='/shared/song.flac')
        upsert_mapping(conn, 'track', tid, 'navidrome', 'private-song', 'own:7',
                       server_path='/private/song.flac')
        conn.commit()
    monkeypatch.setattr(sql_util, '_resolve_scope', lambda _: scope)
    assert [e['path'] for e in db.get_all_library_tracks_for_export('navidrome')] == [expected]
    assert [e['file_path'] for e in db.get_tracks_for_m3u_resolution('navidrome')] == [expected]


def test_mapping_schema_upgrade_keeps_existing_identities(tmp_path):
    import sqlite3
    from core.library2.media_mappings import ensure_media_mapping_schema
    conn = sqlite3.connect(tmp_path / 'old.db')
    conn.row_factory = sqlite3.Row
    # Existing catalogue and mapping from before the additive path observation.
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.execute('ALTER TABLE lib2_media_server_mappings DROP COLUMN server_path')
    conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id) "
                 "VALUES('track',7,'navidrome','nd-old')")
    ensure_media_mapping_schema(conn)
    ensure_media_mapping_schema(conn)
    assert tuple(conn.execute('SELECT server_id, server_path FROM lib2_media_server_mappings').fetchone()) == ('nd-old', None)
    conn.close()
