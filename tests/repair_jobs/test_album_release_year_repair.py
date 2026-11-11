"""Unit tests for AlbumReleaseYearRepairJob.

Covers:
- Registration and category metadata
- Year extraction and folder name calculation
- Safe folder detection and renaming with DB track path updates
- MusicBrainz canonical year resolution waterfall
- Dry-run finding creation
- Live execution and repair worker fix handler
"""

from __future__ import annotations

import os
import struct
from unittest.mock import MagicMock

from core.repair_jobs import get_all_jobs
from core.repair_jobs.album_release_year_repair import (
    AlbumReleaseYearRepairJob,
    apply_album_year_fix,
    compute_new_folder_name,
    extract_year,
    find_album_folder,
    is_folder_exclusive_to_album,
    read_file_year_tags,
    rename_album_folder,
    resolve_canonical_album_year,
    write_release_year_tags,
)
from core.repair_jobs.base import JobContext
from core.repair_worker import FINDING_TYPE_META, JOB_CATEGORIES, RepairWorker
from database.music_database import MusicDatabase
from tests import lib2_seed


def _make_flac(path, tags=None):
    """Create a minimal valid FLAC file with metadata tags."""
    from mutagen.flac import FLAC

    path.parent.mkdir(parents=True, exist_ok=True)
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    path.write_bytes(b"fLaC" + block_header + bytes(si) + bytes(range(256)) * 8)
    audio = FLAC(str(path))
    for k, v in (tags or {}).items():
        audio[k] = v if isinstance(v, list) else [v]
    audio.save()


class _MockConfig:
    def __init__(self, settings=None):
        self._settings = settings or {}

    def get(self, key, default=None):
        return self._settings.get(key, default)


def test_job_registration():
    all_jobs = get_all_jobs()
    assert 'album_release_year_repair' in all_jobs
    job_cls = all_jobs['album_release_year_repair']
    assert job_cls.job_id == 'album_release_year_repair'
    assert job_cls.display_name == 'Album Release Year Alignment'
    assert job_cls.writes_library_files is True


def test_metadata_and_category_registered():
    assert 'album_release_year_mismatch' in FINDING_TYPE_META
    meta = FINDING_TYPE_META['album_release_year_mismatch']
    assert meta['label'] == 'Album Release Year Mismatch'
    assert meta['verb'] == 'Fix Release Year'

    assert 'album_release_year_repair' in JOB_CATEGORIES
    assert JOB_CATEGORIES['album_release_year_repair'] == 'Tags & metadata'


def test_extract_year():
    assert extract_year("1978") == "1978"
    assert extract_year("1978-11-10") == "1978"
    assert extract_year("2011/09/20") == "2011"
    assert extract_year(2023) == "2023"
    assert extract_year("No year here") is None
    assert extract_year(None) is None


def test_compute_new_folder_name():
    # Standard replacement of old year with canonical year
    assert compute_new_folder_name("Jazz (2011)", "Jazz", "2011", "1978") == "Jazz (1978)"
    assert compute_new_folder_name("2011 - Jazz", "Jazz", "2011", "1978") == "1978 - Jazz"
    assert compute_new_folder_name("Jazz [2011]", "Jazz", "2011", "1978") == "Jazz [1978]"

    # Already has canonical year
    assert compute_new_folder_name("Jazz (1978)", "Jazz", "2011", "1978") is None

    # No year in folder
    assert compute_new_folder_name("Jazz", "Jazz", "2011", "1978") is None

    # Title itself contains a year: "1984"
    # When folder has "1984 (2015 Remaster)", only the 2015 should be changed to 1984
    res = compute_new_folder_name("1984 (2015 Remaster)", "1984", "2015", "1984")
    assert res == "1984 (1984 Remaster)"

    # Folder is just the title "1984"
    assert compute_new_folder_name("1984", "1984", "2015", "1984") is None


def test_find_album_folder(tmp_path):
    f1 = tmp_path / "Queen" / "Jazz (2011)" / "01.flac"
    f2 = tmp_path / "Queen" / "Jazz (2011)" / "02.flac"
    expected = str(tmp_path / "Queen" / "Jazz (2011)")
    assert find_album_folder([str(f1), str(f2)]) == os.path.abspath(expected)

    # Multi-disc subdirectory ascends to parent
    f_cd1 = tmp_path / "Queen" / "Jazz (2011)" / "CD 1" / "01.flac"
    f_cd2 = tmp_path / "Queen" / "Jazz (2011)" / "CD 2" / "01.flac"
    assert find_album_folder([str(f_cd1), str(f_cd2)]) == os.path.abspath(expected)


def test_is_folder_exclusive_to_album(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True, exist_ok=True)
    t1 = str(folder / "01.flac")
    t2 = str(folder / "02.flac")

    with db._get_connection() as conn:
        lib2_seed.artist(conn, 'Artist')
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (10, 1, 'Album 1')")
        lib2_seed.file_track(conn, 101, 10, 'T1', t1)
        conn.commit()

    # Folder has only tracks from album 10
    assert is_folder_exclusive_to_album(db, str(folder), 10) is True
    # For another album, it is not exclusive
    assert is_folder_exclusive_to_album(db, str(folder), 99) is False

    # Insert a track from another album into the same folder
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (20, 1, 'Album 2')")
        lib2_seed.file_track(conn, 102, 20, 'T2', t2)
        conn.commit()

    # Now folder contains multiple albums
    assert is_folder_exclusive_to_album(db, str(folder), 10) is False


def test_read_and_write_release_year_tags(tmp_path):
    flac_path = tmp_path / "track.flac"
    _make_flac(flac_path, {'DATE': '2011', 'ALBUM': 'Jazz'})

    tags = read_file_year_tags(str(flac_path))
    assert tags['date'] == '2011'
    assert tags['original_date'] is None

    # Write canonical release year 1978
    ok = write_release_year_tags(str(flac_path), canonical_year="1978", canonical_date="1978-11-10", update_date_tag=True)
    assert ok is True

    updated_tags = read_file_year_tags(str(flac_path))
    assert updated_tags['original_date'] == '1978-11-10'
    assert updated_tags['original_year'] == '1978'
    assert updated_tags['date'] == '1978-11-10'


def test_rename_album_folder_updates_db(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    artist_dir = tmp_path / "Queen"
    old_folder = artist_dir / "Jazz (2011)"
    old_folder.mkdir(parents=True, exist_ok=True)
    t1_old = old_folder / "01.flac"
    t1_old.write_bytes(b"dummy")

    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=2011)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', str(t1_old))
        conn.commit()

    new_folder_path = rename_album_folder(db, str(old_folder), "Jazz (1978)", 1)
    assert new_folder_path is not None
    assert os.path.isdir(new_folder_path)
    assert not os.path.exists(str(old_folder))

    # Verify track file_path in DB was updated
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM lib2_track_files WHERE track_id = 1")
        new_db_fp = cursor.fetchone()[0]
        assert "Jazz (1978)" in new_db_fp
        assert os.path.exists(new_db_fp)


def test_resolve_canonical_album_year_waterfall():
    mb_client = MagicMock()

    # Case 1: Stored release ID
    mb_client.get_release.return_value = {
        'id': 'rel-123',
        'title': 'Jazz',
        'date': '2011-09-20',
        'release-group': {
            'id': 'rg-456',
            'first-release-date': '1978-11-10',
        },
    }

    res = resolve_canonical_album_year(
        mb_client=mb_client,
        album_title='Jazz',
        artist_name='Queen',
        musicbrainz_release_id='rel-123',
    )
    assert res is not None
    year, date_str, rel_id, rg_id = res
    assert year == "1978"
    assert date_str == "1978-11-10"
    assert rel_id == "rel-123"
    assert rg_id == "rg-456"

    # Case 2: Barcode search
    mb_client.reset_mock()
    mb_client.search_release_by_barcode.return_value = [{
        'id': 'rel-bar-1',
        'title': 'Jazz',
        'date': '2011',
        'release-group': {
            'id': 'rg-bar-1',
            'first-release-date': '1978-11-10',
        },
    }]

    res_bar = resolve_canonical_album_year(
        mb_client=mb_client,
        album_title='Jazz',
        artist_name='Queen',
        barcode='0077774621021',
    )
    assert res_bar is not None
    assert res_bar[0] == "1978"

    # Case 3: Title + Artist search fallback
    mb_client.reset_mock()
    mb_client.search_release.return_value = [{
        'id': 'rel-search-1',
        'title': 'Jazz',
        'release-group': {
            'id': 'rg-search-1',
            'first-release-date': '1978',
        },
    }]

    res_search = resolve_canonical_album_year(
        mb_client=mb_client,
        album_title='Jazz',
        artist_name='Queen',
    )
    assert res_search is not None
    assert res_search[0] == "1978"


def test_scan_dry_run_generates_findings(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "test.db"))
    album_dir = tmp_path / "Queen" / "Jazz (2011)"
    f1 = album_dir / "01.flac"
    _make_flac(f1, {'DATE': '2011', 'ALBUM': 'Jazz', 'ARTIST': 'Queen'})

    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=2011)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', str(f1))
        conn.commit()

    mb_client = MagicMock()
    mb_client.search_release.return_value = [{
        'id': 'rel-123',
        'title': 'Jazz',
        'release-group': {'id': 'rg-123', 'first-release-date': '1978-11-10'},
    }]

    findings = []

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True

    ctx = JobContext(
        db=db,
        transfer_folder=str(tmp_path),
        config_manager=_MockConfig({'repair.jobs.album_release_year_repair.settings': {'dry_run': True}}),
        mb_client=mb_client,
        create_finding=create_finding,
    )

    import core.repair_jobs.album_release_year_repair as repair
    original_resolve = repair.resolve_canonical_album_year
    requested_titles = []
    def resolve_with_titles(**kwargs):
        requested_titles.extend(kwargs.get('track_titles') or [])
        return original_resolve(**kwargs)
    monkeypatch.setattr(repair, 'resolve_canonical_album_year', resolve_with_titles)
    job = AlbumReleaseYearRepairJob()
    result = job.scan(ctx)
    assert requested_titles == ['Mustapha']


    assert result.scanned == 1
    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f['finding_type'] == 'album_release_year_mismatch'
    assert f['details']['canonical_year'] == '1978'
    assert f['details']['current_year'] == '2011'
    assert f['details']['new_folder_name'] == 'Jazz (1978)'

    # In dry run, files and DB are untouched
    assert os.path.exists(str(f1))
    with db._get_connection() as conn:
        yr = conn.execute("SELECT year FROM lib2_albums WHERE id = 1").fetchone()[0]
        assert yr == 2011


def test_repair_worker_fix_execution(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    album_dir = tmp_path / "Queen" / "Jazz (2011)"
    f1 = album_dir / "01.flac"
    _make_flac(f1, {'DATE': '2011', 'ALBUM': 'Jazz', 'ARTIST': 'Queen'})

    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=2011)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', str(f1))
        conn.commit()

    worker = RepairWorker(db, transfer_folder=str(tmp_path))
    worker._config_manager = _MockConfig()

    details = {
        'album_id': 1,
        'canonical_year': '1978',
        'canonical_date': '1978-11-10',
        'tracks': [{'id': 1, 'file_path': str(f1)}],
        'folder_path': str(album_dir),
        'new_folder_name': 'Jazz (1978)',
    }

    outcome = worker._execute_fix('album_release_year_mismatch', 'album', 'lib2:1', str(f1), details)
    assert outcome['success'] is True
    assert outcome['action'] == 'aligned_release_year'

    # Verify folder was renamed
    new_dir = tmp_path / "Queen" / "Jazz (1978)"
    assert os.path.isdir(new_dir)
    assert not os.path.exists(album_dir)

    # Verify audio file tags
    new_f1 = new_dir / "01.flac"
    tags = read_file_year_tags(str(new_f1))
    assert tags['original_year'] == '1978'
    assert tags['date'] == '1978-11-10'

    # Verify database album year was updated
    with db._get_connection() as conn:
        yr = conn.execute("SELECT year FROM lib2_albums WHERE id = 1").fetchone()[0]
        assert yr == 1978
        new_fp = conn.execute("SELECT path FROM lib2_track_files WHERE track_id = 1").fetchone()[0]
        assert "Jazz (1978)" in new_fp


def test_a_failed_catalogue_update_puts_the_folder_back(tmp_path):
    old_folder = tmp_path / "Queen" / "Jazz (2011)"
    old_folder.mkdir(parents=True)
    (old_folder / "01.flac").write_bytes(b"dummy")

    class BrokenDb:
        def _get_connection(self):
            raise RuntimeError("database is locked")

    assert rename_album_folder(BrokenDb(), str(old_folder), "Jazz (1978)", 1) is None
    assert old_folder.is_dir() and not (tmp_path / "Queen" / "Jazz (1978)").exists()


def test_rename_album_folder_docker_paths(tmp_path):
    """Test that tracks with server-side/Docker paths (e.g. /media/Queen/Jazz (2011)/01.flac)
    still have their DB path updated when the folder on the host is renamed."""
    db = MusicDatabase(str(tmp_path / "test.db"))
    host_album_dir = tmp_path / "host_music" / "Queen" / "Jazz (2011)"
    host_album_dir.mkdir(parents=True, exist_ok=True)
    docker_fp = "/docker_media/Queen/Jazz (2011)/01.flac"

    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=2011)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', docker_fp)
        conn.commit()

    renamed = rename_album_folder(db, str(host_album_dir), "Jazz (1978)", 1)
    assert renamed is not None
    assert os.path.basename(renamed) == "Jazz (1978)"

    # Verify track in DB was updated correctly despite host vs docker path prefix difference
    with db._get_connection() as conn:
        new_db_fp = conn.execute("SELECT path FROM lib2_track_files WHERE track_id = 1").fetchone()[0]
        assert new_db_fp == "/docker_media/Queen/Jazz (1978)/01.flac"



def test_repair_worker_fix_execution_with_text_album_id(tmp_path):
    """#1633 on Library v2: a Navidrome-backed album's TEXT id is its server
    id; the finding names the catalogue row, and the fix must not trip over
    the text id riding in the details."""
    db = MusicDatabase(str(tmp_path / "test.db"))
    soul_id = "2G0CgWoRXSwDbwnkmPpg6e"
    album_dir = tmp_path / "10 Years" / "Deconstructed (2022)"
    f1 = album_dir / "01.flac"
    _make_flac(f1, {'DATE': '2022', 'ALBUM': 'Deconstructed', 'ARTIST': '10 Years'})

    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, '10 Years', 'Deconstructed', year=2022,
                                   server_source='navidrome', server_id=soul_id)
        lib2_seed.file_track(conn, 1, album_id, 'Track 1', str(f1))
        conn.commit()

    worker = RepairWorker(db, transfer_folder=str(tmp_path))
    worker._config_manager = _MockConfig()

    details = {
        'album_id': soul_id,
        'canonical_year': '2021',
        'canonical_date': '2021-06-04',
        'tracks': [{'id': 'lib2:1', 'file_path': str(f1)}],
        'folder_path': str(album_dir),
        'new_folder_name': 'Deconstructed (2021)',
    }

    outcome = worker._execute_fix('album_release_year_mismatch', 'album', f'lib2:{album_id}', str(f1), details)
    assert outcome['success'] is True
    assert outcome['action'] == 'aligned_release_year'

    new_dir = tmp_path / "10 Years" / "Deconstructed (2021)"
    assert os.path.isdir(new_dir)
    assert not os.path.exists(album_dir)
    tags = read_file_year_tags(str(new_dir / "01.flac"))
    assert tags['original_year'] == '2021'
    assert tags['date'] == '2021-06-04'

    with db._get_connection() as conn:
        assert conn.execute("SELECT year FROM lib2_albums WHERE id = ?", (album_id,)).fetchone()[0] == 2021
        new_fp = conn.execute("SELECT path FROM lib2_track_files WHERE track_id = 1").fetchone()[0]
        assert "Deconstructed (2021)" in new_fp


def test_rename_leaves_another_librarys_copy_where_it_is(tmp_path):
    """third #1062 review G11-4: the folder-name fallback also rewrote another
    library's copy of the album (a same-named folder elsewhere) into a folder
    that was never renamed: it read as missing and was re-downloaded"""
    db = MusicDatabase(str(tmp_path / "test.db"))
    shared = tmp_path / "music" / "Queen" / "Jazz (2011)"
    own = tmp_path / "own" / "bob" / "Queen" / "Jazz (2011)"
    for folder in (shared, own):
        folder.mkdir(parents=True)
        (folder / "01.flac").write_bytes(b"dummy")
    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=2011)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', str(shared / "01.flac"))
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(1, ?)", (str(own / "01.flac"),))
        conn.commit()

    assert rename_album_folder(db, str(shared), "Jazz (1978)", album_id)

    with db._get_connection() as conn:
        paths = sorted(r[0] for r in conn.execute("SELECT path FROM lib2_track_files"))
    assert str(own / "01.flac") in paths
    assert str(shared.parent / "Jazz (1978)" / "01.flac") in paths


# --- #2053: a stored id that names some other release, and compilations ---

_EAGLES_BOOTLEG = {
    'id': 'rel-bootleg', 'title': 'Eagles', 'date': '1996', 'status': 'Bootleg',
    'release-group': {'id': 'rg-live', 'first-release-date': '1996',
                      'secondary-types': ['Live']},
    'media': [{'tracks': [{'title': 'Hotel California'}, {'title': 'Take It Easy'},
                          {'title': 'Get Over It'}]}],
}


def _eagles_recordings(title, artist, limit=25):
    def rel(rel_id, rg_id, types, status='Official'):
        return {'id': rel_id, 'title': 'Eagles', 'status': status,
                'release-group': {'id': rg_id, 'secondary-types': types}}
    return [{'title': title, 'releases': [
        rel('rel-comp', 'rg-comp', ['Compilation']),
        rel('rel-1972', 'rg-1972', None),
        rel('rel-comp2', 'rg-comp2', ['Compilation'], status=None),
    ]}]


def _eagles_client():
    mb_client = MagicMock()
    mb_client.get_release.return_value = _EAGLES_BOOTLEG
    mb_client.search_recording.side_effect = _eagles_recordings
    mb_client.get_release_group.side_effect = lambda rg_id, includes=None: {
        'rg-1972': {'id': 'rg-1972', 'first-release-date': '1972-06-01'},
        'rg-comp': {'id': 'rg-comp', 'first-release-date': '2005-03-15',
                    'secondary-types': ['Compilation']},
    }.get(rg_id)
    return mb_client


def test_a_stored_bootleg_id_does_not_date_the_studio_album():
    # the eagles' 1972 album, three tracks held, carried the id of a 1996 live
    # bootleg with the same name. one song in common is not the same album
    res = resolve_canonical_album_year(
        mb_client=_eagles_client(), album_title='Eagles', artist_name='Eagles',
        musicbrainz_release_id='rel-bootleg',
        track_titles=['Take It Easy', 'Witchy Woman', 'Peaceful Easy Feeling'],
    )
    assert res is not None
    assert (res[0], res[3]) == ('1972', 'rg-1972')


def test_the_studio_album_wins_even_when_a_compilation_shows_up_more_often():
    # a search answer lists only some of a recording's releases. on one live
    # call the first song came back on the compilation alone, which made the
    # compilation the "clear winner" over the album the songs were written for
    mb_client = _eagles_client()
    mb_client.get_release.return_value = {
        **_EAGLES_BOOTLEG,
        'release-group': {**_EAGLES_BOOTLEG['release-group'], 'disambiguation': 'bootleg'},
    }

    def recordings(title, artist, limit=25):
        comp = {'id': 'rel-comp', 'title': 'Eagles', 'status': 'Official',
                'release-group': {'id': 'rg-comp', 'secondary-types': ['Compilation']}}
        album = {'id': 'rel-1972', 'title': 'Eagles', 'status': 'Official',
                 'release-group': {'id': 'rg-1972', 'secondary-types': None}}
        return [{'title': title, 'releases': [comp] if title == 'Take It Easy' else [comp, album]}]
    mb_client.search_recording.side_effect = recordings
    res = resolve_canonical_album_year(
        mb_client=mb_client, album_title='Eagles', artist_name='Eagles',
        musicbrainz_release_id='rel-bootleg',
        track_titles=['Take It Easy', 'Witchy Woman', 'Peaceful Easy Feeling'],
    )
    assert res is not None
    assert (res[0], res[3]) == ('1972', 'rg-1972')


def test_a_live_album_that_carries_the_tracks_keeps_its_own_year():
    mb_client = _eagles_client()
    res = resolve_canonical_album_year(
        mb_client=mb_client, album_title='Eagles', artist_name='Eagles',
        musicbrainz_release_id='rel-bootleg',
        track_titles=['Hotel California', 'Take It Easy', 'Get Over It'],
    )
    assert res is not None and res[0] == '1996'
    mb_client.search_recording.assert_not_called()


def test_a_refuted_id_with_no_clear_studio_album_is_not_guessed():
    mb_client = _eagles_client()
    # two plain albums tie: nothing says which
    mb_client.search_recording.side_effect = lambda title, artist, limit=25: [{
        'title': title, 'releases': [
            {'id': 'a', 'title': 'Eagles', 'status': 'Official', 'release-group': {'id': 'rg-a'}},
            {'id': 'b', 'title': 'Eagles', 'status': 'Official', 'release-group': {'id': 'rg-b'}},
        ]}]
    # either would have a year to give, were one of them picked
    mb_client.get_release_group.side_effect = lambda rg_id, includes=None: {
        'id': rg_id, 'first-release-date': '1972-06-01'}
    res = resolve_canonical_album_year(
        mb_client=mb_client, album_title='Eagles', artist_name='Eagles',
        musicbrainz_release_id='rel-bootleg',
        track_titles=['Witchy Woman', 'Peaceful Easy Feeling'],
    )
    assert res is None


def test_one_held_track_cannot_refute_a_stored_id():
    res = resolve_canonical_album_year(
        mb_client=_eagles_client(), album_title='Eagles', artist_name='Eagles',
        musicbrainz_release_id='rel-bootleg', track_titles=['Witchy Woman'],
    )
    assert res is not None and res[0] == '1996'


def test_song_title_drops_pressing_words_only():
    from core.repair_jobs.album_release_year_repair import song_title
    assert song_title('Hotel California - 2013 Remaster') == 'Hotel California'
    assert song_title('Precious (Remastered)') == 'Precious'
    assert song_title('Enjoy the Silence (Single Version)') == 'Enjoy the Silence'
    assert song_title('Precious (Sasha Remix)') == 'Precious (Sasha Remix)'
    assert song_title('Take It Easy (Live)') == 'Take It Easy (Live)'
    assert song_title('Remastered') == 'Remastered'


def test_track_original_dates_come_from_the_release_track_list():
    from core.repair_jobs.album_release_year_repair import release_track_original_dates
    mb_client = MagicMock()
    mb_client.get_release.return_value = {'media': [
        {'tracks': [
            {'title': 'Precious', 'recording': {'first-release-date': '2005-09-30'}},
            {'title': 'Just Can’t Get Enough', 'recording': {'first-release-date': '1981-09-07'}},
            {'title': 'Martyr', 'recording': {'first-release-date': '2006-09'}},
            {'title': 'No Date', 'recording': {}},
            {'title': 'Twice', 'recording': {'first-release-date': '1984'}},
            {'title': 'From The Future', 'recording': {'first-release-date': '2011'}},
        ]},
        {'tracks': [
            {'title': 'Precious (Sasha remix)', 'recording': {'first-release-date': '2006-09'}},
            {'title': 'Twice', 'recording': {'first-release-date': '1996'}},
        ]},
    ]}
    memo = {}
    dates = release_track_original_dates(mb_client, 'rel-best', memo, not_after='2006')
    assert dates['precious'] == ('2005', '2005-09-30')
    assert dates["just can't get enough"] == ('1981', '1981-09-07')
    assert dates['martyr'] == ('2006', '2006-09')
    assert dates['precious (sasha remix)'] == ('2006', '2006-09')
    # no date, two different years for one title, newer than the album: no answer
    assert 'no date' not in dates and 'twice' not in dates and 'from the future' not in dates
    mb_client.get_release.assert_called_once_with('rel-best', includes=['recordings'])
    # one lookup per album, however often it is asked
    release_track_original_dates(mb_client, 'rel-best', memo, not_after='2006')
    assert mb_client.get_release.call_count == 1
    assert release_track_original_dates(mb_client, None, memo) == {}


def _compilation_fixture(tmp_path, settings, tags2=None):
    db = MusicDatabase(str(tmp_path / "test.db"))
    album_dir = tmp_path / "Depeche Mode" / "2006 - The Best of Depeche Mode"
    f1, f2, f3 = album_dir / "01.flac", album_dir / "02.flac", album_dir / "03.flac"
    _make_flac(f1, {'DATE': '2006', 'ORIGINALDATE': '2006'})
    _make_flac(f2, tags2 or {'DATE': '2006', 'ORIGINALDATE': '2006'})
    _make_flac(f3, {'DATE': '2006', 'ORIGINALDATE': '1999'})
    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Depeche Mode', 'The Best of Depeche Mode', year=2006)
        lib2_seed.file_track(conn, 1, album_id, 'Precious', str(f1))
        lib2_seed.file_track(conn, 2, album_id, 'Just Can\'t Get Enough', str(f2))
        lib2_seed.file_track(conn, 3, album_id, 'Some Obscure B Side', str(f3))
        conn.commit()

    mb_client = MagicMock()
    mb_client.search_release.return_value = [{
        'id': 'rel-best', 'title': 'The Best of Depeche Mode',
        'release-group': {'id': 'rg-best', 'first-release-date': '2006-11-13',
                          'secondary-types': ['Compilation']},
    }]
    mb_client.get_release.return_value = {'id': 'rel-best', 'media': [{'tracks': [
        {'title': 'Precious (Remastered)', 'recording': {'first-release-date': '2005-10-03'}},
        {'title': 'Just Can’t Get Enough', 'recording': {'first-release-date': '1981-09-07'}},
        {'title': 'Some Obscure B Side', 'recording': {}},
    ]}]}

    findings = []
    ctx = JobContext(
        db=db, transfer_folder=str(tmp_path),
        config_manager=_MockConfig({'repair.jobs.album_release_year_repair.settings': settings}),
        mb_client=mb_client,
        create_finding=lambda **kw: findings.append(kw) or True,
    )
    return db, ctx, mb_client, findings, (f1, f2, f3), album_id


def test_compilation_songs_are_left_alone_unless_asked(tmp_path):
    _db, ctx, mb_client, findings, _files, _aid = _compilation_fixture(tmp_path, {'dry_run': True})
    AlbumReleaseYearRepairJob().scan(ctx)
    assert findings == []
    mb_client.get_release.assert_not_called()


def test_compilation_songs_get_the_year_they_first_came_out(tmp_path):
    from mutagen.flac import FLAC
    settings = {'dry_run': True, 'compilation_track_years': True}
    db, ctx, _mb, findings, (f1, f2, f3), album_id = _compilation_fixture(tmp_path, settings)
    AlbumReleaseYearRepairJob().scan(ctx)
    assert len(findings) == 1
    details = findings[0]['details']
    assert details['canonical_year'] == '2006'
    assert details['new_folder_name'] is None
    by_title = {t['title']: t for t in details['tracks']}
    assert by_title['Precious']['original_year'] == '2005'
    assert by_title["Just Can't Get Enough"]['original_year'] == '1981'
    assert by_title['Some Obscure B Side'].get('original_year') is None
    assert by_title['Some Obscure B Side']['keep_original'] is True
    assert '2 song(s) get the year they first came out (1981 to 2005)' in findings[0]['description']
    assert 'Year 2006 is right' in findings[0]['description']

    res = apply_album_year_fix(
        db=db, album_id=str(album_id), canonical_year='2006', canonical_date='2006-11-13',
        tracks=details['tracks'], transfer_folder=str(tmp_path),
    )
    assert res['success'] and res['errors'] == 0
    a1, a2, a3 = FLAC(str(f1)), FLAC(str(f2)), FLAC(str(f3))
    assert (a1['ORIGINALYEAR'], a1['ORIGINALDATE'], a1['DATE']) == (['2005'], ['2005-10-03'], ['2006-11-13'])
    assert (a2['ORIGINALYEAR'], a2['DATE']) == (['1981'], ['2006-11-13'])
    # nothing was found for it: the original date it had stays
    assert a3['ORIGINALDATE'] == ['1999'] and a3['DATE'] == ['2006-11-13']
    with db._get_connection() as conn:
        assert conn.execute("SELECT year FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0] == 2006

    # and the next scan has nothing left to say (no flip back to the album year)
    findings.clear()
    AlbumReleaseYearRepairJob().scan(ctx)
    assert findings == []


def test_a_same_year_finding_says_what_it_writes(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    f1 = tmp_path / "Queen" / "Jazz (1978)" / "01.flac"
    _make_flac(f1, {'DATE': '1978'})
    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, 'Queen', 'Jazz', year=1978)
        lib2_seed.file_track(conn, 1, album_id, 'Mustapha', str(f1))
        conn.commit()
    mb_client = MagicMock()
    mb_client.search_release.return_value = [{
        'id': 'rel-123', 'title': 'Jazz',
        'release-group': {'id': 'rg-123', 'first-release-date': '1978-11-10'},
    }]
    findings = []
    ctx = JobContext(
        db=db, transfer_folder=str(tmp_path),
        config_manager=_MockConfig({'repair.jobs.album_release_year_repair.settings': {'dry_run': True}}),
        mb_client=mb_client, create_finding=lambda **kw: findings.append(kw) or True,
    )
    AlbumReleaseYearRepairJob().scan(ctx)
    assert len(findings) == 1
    assert 'Year 1978 is right; the files have no original date tag yet' in findings[0]['description']
    assert '→' not in findings[0]['description']
