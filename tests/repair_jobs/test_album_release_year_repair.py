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
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (10, 1, 'Album 1')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (101, 10, 1, 'T1', ?)", (t1,))
        conn.commit()

    # Folder has only tracks from album 10
    assert is_folder_exclusive_to_album(db, str(folder), 10) is True
    # For another album, it is not exclusive
    assert is_folder_exclusive_to_album(db, str(folder), 99) is False

    # Insert a track from another album into the same folder
    with db._get_connection() as conn:
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (20, 1, 'Album 2')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (102, 20, 1, 'T2', ?)", (t2,))
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
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Queen')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year) VALUES (1, 1, 'Jazz', 2011)")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'Mustapha', ?)", (str(t1_old),))
        conn.commit()

    new_folder_path = rename_album_folder(db, str(old_folder), "Jazz (1978)", 1)
    assert new_folder_path is not None
    assert os.path.isdir(new_folder_path)
    assert not os.path.exists(str(old_folder))

    # Verify track file_path in DB was updated
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM tracks WHERE id = 1")
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


def test_scan_dry_run_generates_findings(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    album_dir = tmp_path / "Queen" / "Jazz (2011)"
    f1 = album_dir / "01.flac"
    _make_flac(f1, {'DATE': '2011', 'ALBUM': 'Jazz', 'ARTIST': 'Queen'})

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Queen')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year) VALUES (1, 1, 'Jazz', 2011)")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'Mustapha', ?)", (str(f1),))
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

    job = AlbumReleaseYearRepairJob()
    result = job.scan(ctx)

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
        yr = conn.execute("SELECT year FROM albums WHERE id = 1").fetchone()[0]
        assert yr == 2011


def test_repair_worker_fix_execution(tmp_path):
    db = MusicDatabase(str(tmp_path / "test.db"))
    album_dir = tmp_path / "Queen" / "Jazz (2011)"
    f1 = album_dir / "01.flac"
    _make_flac(f1, {'DATE': '2011', 'ALBUM': 'Jazz', 'ARTIST': 'Queen'})

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Queen')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year) VALUES (1, 1, 'Jazz', 2011)")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'Mustapha', ?)", (str(f1),))
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

    outcome = worker._execute_fix('album_release_year_mismatch', 'album', '1', str(f1), details)
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
        yr = conn.execute("SELECT year FROM albums WHERE id = 1").fetchone()[0]
        assert yr == 1978
        new_fp = conn.execute("SELECT file_path FROM tracks WHERE id = 1").fetchone()[0]
        assert "Jazz (1978)" in new_fp


def test_rename_album_folder_docker_paths(tmp_path):
    """Test that tracks with server-side/Docker paths (e.g. /media/Queen/Jazz (2011)/01.flac)
    still have their DB path updated when the folder on the host is renamed."""
    db = MusicDatabase(str(tmp_path / "test.db"))
    host_album_dir = tmp_path / "host_music" / "Queen" / "Jazz (2011)"
    host_album_dir.mkdir(parents=True, exist_ok=True)
    docker_fp = "/docker_media/Queen/Jazz (2011)/01.flac"

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Queen')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year) VALUES (1, 1, 'Jazz', 2011)")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'Mustapha', ?)", (docker_fp,))
        conn.commit()

    renamed = rename_album_folder(db, str(host_album_dir), "Jazz (1978)", 1)
    assert renamed is not None
    assert os.path.basename(renamed) == "Jazz (1978)"

    # Verify track in DB was updated correctly despite host vs docker path prefix difference
    with db._get_connection() as conn:
        new_db_fp = conn.execute("SELECT file_path FROM tracks WHERE id = 1").fetchone()[0]
        assert new_db_fp == "/docker_media/Queen/Jazz (1978)/01.flac"

