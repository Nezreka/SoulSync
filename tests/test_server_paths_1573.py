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


@pytest.mark.parametrize('reported_local', [True, False])
def test_relative_library_configuration_returns_absolute_identity(tmp_path, library, monkeypatch, reported_local):
    monkeypatch.chdir(tmp_path)
    config = _Config(['./Media/Music'])
    path = './Media/Music/' + _REL if reported_local else '/music/' + _REL
    assert server_paths.local_path_for(path, config) == str(library / _REL)
    if not reported_local:
        assert server_paths._learned == {'/music': str(library).replace('\\', '/')}


def test_learned_mount_skips_the_walk(library, monkeypatch):
    server_paths.local_path_for('/music/' + _REL, settings_module.config_manager)
    other = library / 'The Killers' / 'The Killers - Hot Fuss' / '01 - Jenny.flac'
    other.write_bytes(b'audio')
    import core.library.path_resolver as resolver
    monkeypatch.setattr(resolver, 'resolve_library_file_path',
                        lambda *a, **k: pytest.fail('learned mount should not walk'))
    assert server_paths.local_path_for('/music/The Killers/The Killers - Hot Fuss/01 - Jenny.flac') == str(other)


def test_unreachable_namespace_stops_expensive_album_scans(tmp_path, monkeypatch):
    root = tmp_path / 'music'
    album = root / 'A' / 'Physical Album'
    album.mkdir(parents=True)
    (album / 'keep.flac').write_bytes(b'audio')
    config = _Config([str(root)])
    scans = []
    import os
    real_scandir = os.scandir

    def count_scans(path):
        scans.append(path)
        return real_scandir(path)

    monkeypatch.setattr(os, 'scandir', count_scans)
    for i in range(server_paths._GIVE_UP_AFTER):
        assert server_paths.local_path_for(f'/music/A/B/{i}.flac', config) is None
    # Each allowed miss scans the artist once per fuzzy strategy, and its
    # album once for synthesized filenames. Case folding also reads the root
    # once, then reuses its cached listing for every following miss.
    assert scans.count(str(root)) == 1
    assert len(scans) == server_paths._GIVE_UP_AFTER * 3 + 1
    scans_before_give_up = list(scans)
    for i in range(server_paths._GIVE_UP_AFTER, server_paths._GIVE_UP_AFTER + 50):
        assert server_paths.local_path_for(f'/music/A/B/{i}.flac', config) is None
    assert scans == scans_before_give_up



def test_a_hit_resets_the_miss_count(library, monkeypatch):
    """A reachable flat hit re-enables fuzzy scans for its own namespace."""
    for i in range(server_paths._GIVE_UP_AFTER - 1):
        server_paths.local_path_for(f'/music/A/B/{i}.flac', settings_module.config_manager)
    flat = library / 'playlist/song.flac'
    flat.parent.mkdir()
    flat.write_bytes(b'flat hit')
    assert server_paths.local_path_for('/music/playlist/song.flac', settings_module.config_manager) == str(flat)
    # No mount is learned from the two-segment tail, so this requires a real
    # fuzzy directory scan after further misses in that same namespace.
    for i in range(10):
        server_paths.local_path_for(f'/music/A/C/{i}.flac', settings_module.config_manager)
    assert server_paths.local_path_for(
        '/music/The Killers/Wrong Album/11 - Everything Will Be Alright.flac',
        settings_module.config_manager,
    ) == str(library / _REL)


@pytest.mark.parametrize('reported', ['/flat/playlist/song.flac', '/music/playlist/song.flac'])
def test_unreachable_files_do_not_hide_a_later_reachable_flat_path(tmp_path, reported):
    root = tmp_path / 'flat-mounted'
    target = root / 'playlist/song.flac'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'local flat song')
    config = _Config([str(root)])

    for i in range(server_paths._GIVE_UP_AFTER):
        assert server_paths.local_path_for(f'/music/Artist/Album/{i}.flac', config) is None

    assert server_paths.local_path_for(reported, config) == str(target)


def test_miss_budget_does_not_disable_another_namespaces_fuzzy_lookup(library):
    for i in range(server_paths._GIVE_UP_AFTER):
        assert server_paths.local_path_for(
            f'/unmounted/A/B/{i}.flac', settings_module.config_manager,
        ) is None

    assert server_paths.local_path_for(
        '/other-mount/The Killers/Wrong Album/11 - Everything Will Be Alright.flac',
        settings_module.config_manager,
    ) == str(library / _REL)


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


@pytest.mark.parametrize('server', ['navidrome', 'plex', 'jellyfin'])
def test_relative_mount_recognizes_file_without_recording_id_or_parent_mapping(tmp_path, library, monkeypatch, server):
    db, tid = _db(tmp_path, library, server)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings_module, 'config_manager', _Config(['./Media/Music']))
    assert db.insert_or_update_media_track(_Track('new-song', '/music/' + _REL),
                                           'unmapped-edition', 'unmapped-artist', server_source=server)
    assert _paths(db, tid, server) == (str(library / _REL), '/music/' + _REL)


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
    # the old schema had neither the column nor the coverage index built on it
    conn.execute('DROP INDEX IF EXISTS idx_lib2_media_mappings_track_path')
    conn.execute('ALTER TABLE lib2_media_server_mappings DROP COLUMN server_path')
    conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id) "
                 "VALUES('track',7,'navidrome','nd-old')")
    ensure_media_mapping_schema(conn)
    ensure_media_mapping_schema(conn)
    assert tuple(conn.execute('SELECT server_id, server_path FROM lib2_media_server_mappings').fetchone()) == ('nd-old', None)
    # and the upgrade builds the coverage index once the column is back
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index'"
                        " AND name='idx_lib2_media_mappings_track_path'").fetchone()
    conn.close()


def test_a_namespace_that_never_resolves_stops_probing(tmp_path, monkeypatch):
    """3.6.1 stopped after a run of misses. v2 kept the cheap probes going
    for every track of an unmounted library: stats per root per track."""
    from core.library import path_resolver
    calls = []
    monkeypatch.setattr(path_resolver, 'resolve_library_file_path',
                        lambda *a, **k: calls.append(1))
    monkeypatch.setattr(server_paths, '_STOP_AFTER', 30)
    config = _Config([str(tmp_path)])
    for i in range(60):
        assert server_paths.local_path_for(f'/nas/A/B/{i}.flac', config) is None
    assert len(calls) == 30
    # another namespace still gets its own budget
    server_paths.local_path_for('/other/A/B/x.flac', config)
    assert len(calls) == 31


# ---------------------------------------------- a linux path on a windows host
def _as_windows(monkeypatch, windows=True):
    from core.library import path_mappings
    monkeypatch.setattr(path_mappings, "foreign_rooted",
                        lambda p, nt=None: path_mappings.__dict__["_real_foreign"](p, nt=windows))


def test_a_stray_folder_on_the_current_drive_is_not_the_servers_library(tmp_path, monkeypatch):
    """Plex on linux says /mnt/musicBackup. on Windows that reads as
    E:\\mnt\\musicBackup, and a stray folder there (17 files) was taken for the
    library: the folder check said 0% of 311,000 tracks could be read, and a
    file lookup could land on the stray copy. the folder check's own 'same'
    step was guarded; this lookup, asked right after it, was not."""
    stray = tmp_path / "mnt" / "musicBackup" / "Kendrick Lamar"
    stray.mkdir(parents=True)
    (stray / "song.flac").write_bytes(b"x")
    # a path that "exists here" the way /mnt/musicBackup did on that E: drive
    folder = str(tmp_path / "mnt" / "musicBackup").replace("\\", "/")
    if not folder.startswith("/"):
        folder = "/" + folder.split(":", 1)[-1].lstrip("/")
        monkeypatch.chdir(tmp_path.anchor)
    file = folder + "/Kendrick Lamar/song.flac"
    import os
    assert os.path.isdir(folder) and os.path.exists(file)       # the trap is set
    monkeypatch.setattr(server_paths, "_from_override", lambda *a, **k: None)
    monkeypatch.setattr("core.library.path_resolver.resolve_library_file_path", lambda *a, **k: None)
    # on linux it IS the same place
    _as_windows(monkeypatch, windows=False)
    assert server_paths.local_folder_for(folder) == os.path.abspath(folder)
    assert server_paths.local_path_for(file) == os.path.abspath(file)
    # on windows it is never taken as is
    _as_windows(monkeypatch)
    assert server_paths.local_folder_for(folder) is None
    assert server_paths.local_path_for(file) is None


def test_the_folder_check_finds_the_real_folder_past_the_stray_one(monkeypatch):
    """the whole case: the stray folder exists, the catalogue knows the files
    under H:/Music. the check must say H:/Music, learned from the files."""
    from core.library import coverage
    _as_windows(monkeypatch)
    pairs = [(f"H:/Music/Artist {i}/Album/0{i}.flac", f"/mnt/musicBackup/Artist {i}/Album/0{i}.flac")
             for i in range(5)]
    monkeypatch.setattr("core.library2.scan_paths.server_pairs", lambda conn, kind, limit=2000: pairs)
    exists = {"H:/Music", "/mnt/musicBackup", "E:\\mnt\\musicBackup"}
    monkeypatch.setattr(server_paths.os.path, "isdir", lambda p: p in exists)
    env = coverage.Env(library_dirs=["H:/Music"], isdir=lambda p: p in exists,
                       listdir=lambda p: [], learned=server_paths.local_folder_for)
    local, how, _broken = coverage.locate_folder(None, "plex", "/mnt/musicBackup", env, [])
    assert (local, how) == ("H:/Music", "learned")
