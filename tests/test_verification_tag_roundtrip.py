"""SOULSYNC_VERIFICATION file tag: write + read back (travels with the file,
survives DB resets; the AcoustID scan reads it to refresh the DB column)."""

import shutil
import subprocess

import pytest

from core.tag_writer import read_file_tags, write_verification_status


pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='ffmpeg required to build test audio'
)


def _make_flac(path):
    subprocess.run(
        ['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi',
         '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.1', str(path)],
        check=True,
    )


def test_flac_verification_tag_roundtrip(tmp_path):
    f = tmp_path / 'x.flac'
    _make_flac(f)
    assert write_verification_status(str(f), 'force_imported') is True
    tags = read_file_tags(str(f))
    assert tags.get('verification_status') == 'force_imported'


def test_overwrite_existing_status(tmp_path):
    f = tmp_path / 'y.flac'
    _make_flac(f)
    write_verification_status(str(f), 'unverified')
    write_verification_status(str(f), 'verified')
    assert read_file_tags(str(f)).get('verification_status') == 'verified'


def test_missing_file_returns_false_not_raises(tmp_path):
    assert write_verification_status(str(tmp_path / 'nope.flac'), 'verified') is False


def test_db_migration_adds_verification_status_column(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 't.db'))
    with db._get_connection() as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(tracks)').fetchall()]
    assert 'verification_status' in cols
