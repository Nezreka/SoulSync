"""Split server rows remain reviewable without merging native catalogue rows."""
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from core.repair_jobs.album_tag_consistency import AlbumTagConsistencyJob
from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from tests.repair_jobs.test_album_tag_consistency_reporting import _make_flac


@pytest.fixture
def split(imported_conn, legacy_db, tmp_path):
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Split Artist')").lastrowid
    albums, tracks, files, paths = [], [], [], []
    for index, name in enumerate(['Release', 'Wrong tag']):
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,year) VALUES(?,'Release',2020)", (artist,)).lastrowid
        track = conn.execute('INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)', (album, f'Song {index}')).lastrowid
        path = tmp_path / f'{index}.flac'
        _make_flac(path, {'album': name, 'title': f'Keep {index}', 'albumartist': 'Split Artist',
                          'musicbrainz_releasegroupid': 'same-group', 'date': '2020'})
        file = conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (track, str(path))).lastrowid
        albums.append(album); tracks.append(track); files.append(file); paths.append(path)
    conn.commit()
    return SimpleNamespace(conn=conn, db=legacy_db, artist=artist, albums=albums,
                           tracks=tracks, files=files, paths=paths, cfg=SimpleNamespace(get=lambda _k, default=None: default))


def _scan(s, scope=None):
    findings = []
    result = AlbumTagConsistencyJob().scan(JobContext(db=s.db, config_manager=s.cfg,
        transfer_folder=str(s.paths[0].parent), scope=scope,
        create_finding=lambda **kw: findings.append(kw) or True))
    return result, findings


def _apply(s, finding):
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = s.db, s.cfg, str(s.paths[0].parent)
    return worker._fix_album_tag_inconsistency('album', finding['entity_id'], None, finding['details'])


def test_two_single_track_native_rows_create_one_review_and_only_retag_listed_files(split, tmp_path):
    s = split
    result, findings = _scan(s)
    assert result.findings_created == 1
    finding = findings[0]
    assert finding['details']['server_split']
    assert finding['details']['library_v2']['file_ids'] == s.files
    assert [t['file_id'] for t in finding['details']['tracks']] == s.files
    third = tmp_path / 'later.flac'
    _make_flac(third, {'album': 'Later file', 'title': 'Untouched'})
    s.conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (s.tracks[1], str(third)))
    s.conn.commit()
    assert _apply(s, finding)['success']
    assert FLAC(s.paths[1])['album'] == ['Release']
    assert FLAC(s.paths[1])['title'] == ['Keep 1']
    assert FLAC(third)['album'] == ['Later file']
    assert s.conn.execute('SELECT COUNT(*) FROM lib2_albums WHERE id IN (?,?)', s.albums).fetchone()[0] == 2


@pytest.mark.parametrize('conflict', ['embedded_rg', 'embedded_year', 'catalogue_year', 'edition', 'pin', 'owner'])
def test_distinct_identity_or_ownership_is_not_a_split_candidate(split, conflict):
    s = split
    if conflict.startswith('embedded'):
        audio = FLAC(s.paths[1])
        audio['musicbrainz_releasegroupid' if conflict == 'embedded_rg' else 'date'] = ['other-group' if conflict == 'embedded_rg' else '2021']
        audio.save()
    elif conflict == 'catalogue_year':
        s.conn.execute('UPDATE lib2_albums SET year=2021 WHERE id=?', (s.albums[1],))
    elif conflict == 'edition':
        for index, album in enumerate(s.albums):
            s.conn.execute('INSERT INTO lib2_release_editions(release_group_id,title,spotify_id,is_default) VALUES(?,?,?,1)',
                           (album, 'Release', f'different-edition-{index}'))
    elif conflict == 'pin':
        for index, album in enumerate(s.albums):
            s.conn.execute("UPDATE lib2_albums SET canonical_locked=1,canonical_source='spotify',canonical_album_id=? WHERE id=?",
                           (f'pin-{index}', album))
    else:
        s.conn.execute('UPDATE lib2_track_files SET owner_profile_id=2 WHERE id=?', (s.files[1],))
    s.conn.commit()
    assert _scan(s)[1] == []


def test_split_scan_obeys_file_scope(split):
    assert _scan(split, {'file_ids': [split.files[0]], 'file_paths': [str(split.paths[0])]})[1] == []


@pytest.mark.parametrize('changed', ['handtag', 'file_rehomed', 'pin', 'manual'])
def test_split_apply_revalidates_new_protection(split, changed):
    from database.music_database import MusicDatabase
    from core.library2.metadata_overrides import set_field_override
    s = split
    _, findings = _scan(s)
    assert len(findings) == 1
    before = s.paths[1].read_bytes()
    if changed == 'handtag':
        s.db.manual_path_keys = lambda: {MusicDatabase.manual_path_key(str(s.paths[1]))}
    elif changed == 'file_rehomed':
        s.conn.execute('UPDATE lib2_track_files SET track_id=? WHERE id=?', (s.tracks[0], s.files[1]))
    elif changed == 'pin':
        s.conn.execute("UPDATE lib2_albums SET canonical_locked=1,canonical_source='spotify',canonical_album_id='new-pin' WHERE id=?", (s.albums[1],))
    else:
        set_field_override(s.conn, entity_type='release_group', entity_id=s.albums[1], field_name='title', value='Manual title')
    s.conn.commit()
    out = _apply(s, findings[0])
    assert s.paths[1].read_bytes() == before
    if changed in ('file_rehomed', 'pin'):
        assert out['success'] is False
