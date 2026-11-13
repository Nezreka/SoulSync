"""#2072: a compilation with no album artist tag is one album per track artist
on the server. Album Tag Consistency saw nothing: no two of those rows share
an artist, and each holds one track."""
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from core.repair_jobs.album_tag_consistency import (
    AlbumTagConsistencyJob, folder_split_album_artist, release_folder)
from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from tests.repair_jobs.test_album_tag_consistency_reporting import _make_flac

ARTISTS = ['Aphex Twin', 'Booka Shade', 'Kraftwerk', 'Noisia']


def _build(conn, folder, artists, *, title='Wipeout Pulse', tags=None, by_artist=False,
           numbers=None):
    folder.mkdir(parents=True, exist_ok=True)
    albums, paths = [], []
    for index, name in enumerate(artists):
        artist = conn.execute('INSERT INTO lib2_artists(name) VALUES(?)', (name,)).lastrowid
        album = conn.execute('INSERT INTO lib2_albums(primary_artist_id,title,year) VALUES(?,?,2007)',
                             (artist, title)).lastrowid
        conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES(?,?,'primary')",
                     (album, artist))
        number = numbers[index] if numbers else index + 1
        track = conn.execute('INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,?,?)',
                             (album, f'Song {index}', number)).lastrowid
        # an organiser that files by track artist: <shelf>/<artist>/<album>/file
        home = folder / name / title if by_artist else folder
        home.mkdir(parents=True, exist_ok=True)
        path = home / f'{index:02d}.flac'
        _make_flac(path, {'album': title, 'title': f'Song {index}', 'artist': name,
                          'date': '2007', **(tags or {})})
        conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (track, str(path)))
        albums.append(album)
        paths.append(path)
    conn.commit()
    return albums, paths


@pytest.fixture
def env(imported_conn, legacy_db, tmp_path):
    return SimpleNamespace(conn=imported_conn, db=legacy_db, tmp=tmp_path,
                           cfg=SimpleNamespace(get=lambda _k, default=None: default))


def _scan(env):
    findings = []
    AlbumTagConsistencyJob().scan(JobContext(
        db=env.db, config_manager=env.cfg, transfer_folder=str(env.tmp),
        create_finding=lambda **kw: findings.append(kw) or True))
    return [f for f in findings if f['details'].get('folder_split')]


def test_a_compilation_split_by_track_artist_is_reported_and_unified(env):
    albums, paths = _build(env.conn, env.tmp / 'Wipeout Pulse', ARTISTS)
    findings = _scan(env)
    assert len(findings) == 1
    details = findings[0]['details']
    assert sorted(details['album_ids']) == sorted(albums)
    assert details['inconsistencies'][0]['field'] == 'albumartist'
    assert details['inconsistencies'][0]['canonical'] == 'Various Artists'
    assert details['inconsistencies'][0]['outlier_count'] == 4

    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = env.db, env.cfg, str(env.tmp)
    out = worker._fix_album_tag_inconsistency('album', findings[0]['entity_id'], None, details)
    assert out['success'], out
    assert all(FLAC(p)['albumartist'] == ['Various Artists'] for p in paths)
    assert FLAC(paths[0])['artist'] == ['Aphex Twin']
    # tags only: the catalogue rows are left for the next scan to bring together
    assert env.conn.execute('SELECT COUNT(*) FROM lib2_albums WHERE title=?',
                            ('Wipeout Pulse',)).fetchone()[0] == 4


def test_the_album_artist_most_files_already_name_wins():
    tags = [{'albumartist_tag': 'DJ Mix'}, {'albumartist_tag': 'DJ Mix'},
            {'albumartist_tag': None, 'artist_tag': 'Guest'}]
    assert folder_split_album_artist(tags) == 'DJ Mix'
    # nobody agrees and only two names: not enough to call it
    assert folder_split_album_artist(
        [{'albumartist_tag': None, 'artist_tag': 'A'}, {'albumartist_tag': None, 'artist_tag': 'B'}]) is None


def test_two_artists_sharing_a_folder_evenly_is_left_alone(env):
    _build(env.conn, env.tmp / 'Split EP', ARTISTS[:2], title='Split EP')
    assert _scan(env) == []


def test_the_same_title_in_two_folders_is_two_albums(env):
    _build(env.conn, env.tmp / 'a' / 'Greatest Hits', ARTISTS[:2], title='Greatest Hits')
    _build(env.conn, env.tmp / 'b' / 'Greatest Hits', ARTISTS[2:], title='Greatest Hits')
    assert _scan(env) == []


def test_different_years_in_one_folder_stay_apart(env):
    _, paths = _build(env.conn, env.tmp / 'Loose', ARTISTS, title='Loose')
    audio = FLAC(paths[0])
    audio['date'] = ['1999']
    audio.save()
    assert _scan(env) == []


def test_one_track_in_each_artist_folder_is_found_across_them(env):
    albums, paths = _build(env.conn, env.tmp / 'music', ARTISTS, by_artist=True)
    findings = _scan(env)
    assert len(findings) == 1
    assert sorted(findings[0]['details']['album_ids']) == sorted(albums)
    assert 'separate artist folders' in findings[0]['description']
    assert findings[0]['details']['inconsistencies'][0]['canonical'] == 'Various Artists'


def test_every_artist_has_a_greatest_hits(env):
    # same title on the same shelf, but each is its own record: they all start at track 1
    _build(env.conn, env.tmp / 'music', ARTISTS, title='Greatest Hits', by_artist=True,
           numbers=[1, 1, 1, 1])
    assert _scan(env) == []


def test_two_artist_folders_are_not_enough_to_call_it_one_record(env):
    _build(env.conn, env.tmp / 'music', ARTISTS[:2], title='Split', by_artist=True)
    assert _scan(env) == []


def test_disc_folders_are_one_release():
    assert release_folder('/m/Box/CD1/01.flac') == '/m/Box'
    assert release_folder('/m/Box/Disc 2/01.flac') == '/m/Box'
    assert release_folder('/m/Box/01.flac') == '/m/Box'
    assert release_folder('C:\\m\\Box\\CD 1\\01.flac') == 'C:/m/Box'
