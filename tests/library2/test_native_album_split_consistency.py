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


@pytest.fixture
def record(imported_conn, legacy_db, tmp_path):
    """One release opening with a guest-led song (the outlier album tag), two
    songs by the artist, and a fourth owned by a second library."""
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Host')").lastrowid
    guest = conn.execute("INSERT INTO lib2_artists(name) VALUES('Guest')").lastrowid
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Record')", (artist,)).lastrowid
    paths = []
    for index, (lead, owner, tag) in enumerate([(guest, None, 'Record (Deluxe)'), (artist, None, 'Record'),
                                                (artist, None, 'Record'), (artist, 2, 'Record')]):
        track = conn.execute('INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,?,?)',
                             (album, f'Song {index}', index + 1)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)", (track, lead))
        if lead == guest:
            conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'featured',1)", (track, artist))
        path = tmp_path / f'r{index}.flac'
        _make_flac(path, {'album': tag, 'title': f'Song {index}', 'albumartist': 'Host'})
        conn.execute('INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,?,?)', (track, str(path), owner))
        paths.append(path)
    conn.commit()
    return SimpleNamespace(conn=conn, db=legacy_db, artist=artist, album=album, paths=paths,
                           cfg=SimpleNamespace(get=lambda _k, default=None: default))


def test_artist_scope_keeps_guest_led_outliers_and_owners_share_one_finding(record):
    from core.repair_jobs.base import build_artist_file_scope
    for scope in (None, build_artist_file_scope(record.db, record.artist, 'Host')):
        _result, findings = _scan(record, scope)
        album_findings = [f for f in findings if not f['details'].get('server_split')]
        # One finding per release id: per-library findings would overwrite each other.
        assert len(album_findings) == 1
        tracks = album_findings[0]['details']['tracks']
        assert len(tracks) == 4 and {t['owner_profile_id'] for t in tracks} == {None, 2}
        assert album_findings[0]['details']['inconsistencies'][0]['outlier_count'] == 1


def test_subjects_keep_the_release_artist_identity_for_a_guest_led_track(record):
    from core.library2.maintenance_subjects import active_file_subjects
    guest_led = next(s for s in active_file_subjects(record.db, None) if s['title'] == 'Song 0')
    assert guest_led['artist_name'] == 'Guest'
    assert guest_led['artist_id'] == record.artist


def test_a_file_gone_since_the_scan_does_not_fail_the_rest_of_the_album(record):
    _result, findings = _scan(record)
    finding = next(f for f in findings if not f['details'].get('server_split'))
    record.paths[1].unlink()
    assert _apply(record, finding)['success']
    assert FLAC(record.paths[0])['album'] == ['Record']
