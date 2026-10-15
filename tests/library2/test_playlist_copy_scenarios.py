"""Playlist copies across users and partial albums keep every file, owner and origin."""

from pathlib import Path

from core.library2.importer import import_legacy_library
from tests.library2.test_two_libraries import lib  # noqa: F401 - the fixture
from tests.library2.test_two_libraries_import import _legacy, _q


def _playlists(db, rows):
    with db._get_connection() as conn:
        for source_id, name, profile, track_ids in rows:
            pid = conn.execute(
                "INSERT INTO mirrored_playlists(source,source_playlist_id,name,profile_id) "
                "VALUES('spotify',?,?,?)", (source_id, name, profile)).lastrowid
            for position, track in enumerate(track_ids, 1):
                conn.execute("INSERT INTO mirrored_playlist_tracks(playlist_id,position,track_name,"
                             "artist_name,source_track_id) VALUES(?,?, 'Intro','A',?)",
                             (pid, position, track))
        conn.commit()


def _touch(*paths):
    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'audio')


def test_same_playlist_name_in_two_users_keeps_both_copies_and_owners(lib):
    kim_file = Path(lib.kim_root) / 'Mix' / 'A - Intro.flac'
    sam_file = Path(lib.kim_root).parent / 'sam' / 'Mix' / 'A - Intro.flac'
    _touch(kim_file, sam_file)
    _legacy(lib.db,
        artists=[('1', 'A', lib.kim, 'a' * 22), ('2', 'A', lib.sam, 'a' * 22)],
        albums=[('10', '1', 'Record', 1, 'b' * 22, lib.kim, 2020), ('20', '2', 'Record', 1, 'b' * 22, lib.sam, 2020)],
        tracks=[('100', '10', '1', 'Intro', 1, str(kim_file), 'c' * 22, 0, lib.kim, 1),
                ('200', '20', '2', 'Intro', 1, str(sam_file), 'c' * 22, 0, lib.sam, 1)])
    _playlists(lib.db, [('p1', 'Mix', lib.kim, ['c' * 22]), ('p2', 'Mix', lib.sam, ['c' * 22])])
    import_legacy_library(lib.db)
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_tracks WHERE title='Intro'") == [(1,)]
    assert _q(lib.db, 'SELECT path FROM lib2_track_files ORDER BY path') == sorted([(str(kim_file),), (str(sam_file),)])
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_file_origins WHERE playlist_name='Mix'") == [(2,)]
    assert kim_file.exists() and sam_file.exists()


def test_playlist_copy_of_one_track_joins_the_full_album_without_losing_the_rest(lib):
    root = Path(lib.shared)
    album = [root / 'A' / 'Record' / f'0{i} - T{i}.flac' for i in (1, 2)]
    copy = root / 'Mix' / 'A - T1.flac'
    _touch(*album, copy)
    _legacy(lib.db,
        artists=[('1', 'A', None, 'a' * 22), ('2', 'A', None, 'a' * 22)],
        albums=[('10', '1', 'Record', 2, 'b' * 22, None, 2020), ('20', '2', 'Record', 2, 'b' * 22, None, 2020)],
        tracks=[('100', '10', '1', 'T1', 1, str(album[0]), 'c1' * 11, 0, None, 1),
                ('101', '10', '1', 'T2', 2, str(album[1]), 'c2' * 11, 0, None, 1),
                ('200', '20', '2', 'T1', 1, str(copy), 'c1' * 11, 0, None, 1)])
    _playlists(lib.db, [('p1', 'Mix', 1, ['c1' * 11])])
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_albums WHERE title='Record'") == [(1,)]
    assert _q(lib.db, 'SELECT COUNT(*) FROM lib2_tracks') == [(2,)]
    assert _q(lib.db, 'SELECT COUNT(*) FROM lib2_track_files') == [(3,)]
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_file_origins WHERE playlist_name='Mix'") == [(1,)]


def test_import_survives_an_older_playlist_schema_and_a_single_user_install(lib):
    """No owner columns, and mirrored playlists without source track ids."""
    path = Path(lib.shared) / 'A' / 'Record' / '01.flac'
    _touch(path)
    with lib.db._get_connection() as conn:
        conn.executescript('''
            CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, server_source TEXT);
            CREATE TABLE albums(id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, track_count INTEGER, server_source TEXT);
            CREATE TABLE tracks(id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT, track_number INTEGER,
                                file_path TEXT, server_source TEXT, spotify_track_id TEXT);
            INSERT INTO artists VALUES('1','A','plex');
            INSERT INTO albums VALUES('10','1','Record',1,'plex');
            INSERT INTO tracks VALUES('100','10','1','Intro',1,'%s','plex','%s');
            DROP TABLE IF EXISTS mirrored_playlist_tracks;
            CREATE TABLE mirrored_playlist_tracks(playlist_id INTEGER, track_name TEXT);
        ''' % (path, 'c' * 22))
        conn.commit()
    import_legacy_library(lib.db)
    assert _q(lib.db, 'SELECT path FROM lib2_track_files') == [(str(path),)]


def test_versions_name_the_playlist_each_file_was_saved_for(lib):
    from core.library2 import queries as Q

    kim_file = Path(lib.shared) / 'Mix X' / 'A - Intro.flac'
    other = Path(lib.shared) / 'Mix Y' / 'A - Intro.flac'
    _touch(kim_file, other)
    _legacy(lib.db,
        artists=[('1', 'A', None, 'a' * 22), ('2', 'A', None, 'a' * 22)],
        albums=[('10', '1', 'Record', 1, 'b' * 22, None, 2020), ('20', '2', 'Record', 1, 'b' * 22, None, 2020)],
        tracks=[('100', '10', '1', 'Intro', 1, str(kim_file), 'c' * 22, 0, None, 1),
                ('200', '20', '2', 'Intro', 1, str(other), 'c' * 22, 0, None, 1)])
    _playlists(lib.db, [('p1', 'Mix X', 1, ['c' * 22]), ('p2', 'Mix Y', 1, ['c' * 22])])
    import_legacy_library(lib.db)
    track_id = _q(lib.db, "SELECT id FROM lib2_tracks WHERE title='Intro'")[0][0]
    with lib.db._get_connection() as conn:
        versions = Q.track_versions(conn, track_id)
    assert sorted(tuple(v['file']['playlists']) for v in versions) == [('Mix X',), ('Mix Y',)]
