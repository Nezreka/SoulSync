"""the hand-tagged lock on Library v2: a file the user tagged themselves is
remembered by its path, and that remembered file IS the lock -- the maintenance
jobs that would retag, renumber, rematch or delete it consult it, and a rescan
cannot lose it because nothing is stored on the catalogue row.

Upstream also stamps ``metadata_locked`` on its legacy track/album rows; this
branch has no such rows (see ``MusicDatabase._ensure_manual_metadata_schema``).
"""

from __future__ import annotations

from pathlib import Path

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track

LIVE = '/m/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac'


def _db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / 'lib.db'))


def _album_with_file(db, path):
    with db._get_connection() as conn:
        track_id = seed_library_track(conn, artist='Radiohead', album='Live at Glastonbury 2003',
                                      title='Lucky', file_path=path)
        album_id = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
        conn.commit()
    return album_id


def test_the_path_key_is_the_part_every_mount_agrees_on():
    key = MusicDatabase.manual_path_key
    assert key('/app/Transfer/Radiohead/Live 2003/01 - Lucky.flac') == 'radiohead/live 2003/01 - lucky.flac'
    assert key(r'D:\Music\Radiohead\Live 2003\01 - Lucky.flac') == 'radiohead/live 2003/01 - lucky.flac'
    assert key('') == ''


def test_a_file_is_remembered_under_every_mount(tmp_path):
    db = _db(tmp_path)
    # SoulSync wrote it under its own mount...
    db.record_manual_metadata_file('/app/Transfer/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac',
                                   album_title='Live at Glastonbury 2003', album_artist='Radiohead')
    # ...and the media server knows it under its own
    assert MusicDatabase.manual_path_key(LIVE) in db.manual_path_keys()


def test_a_file_already_in_the_library_is_counted(tmp_path):
    db = _db(tmp_path)
    _album_with_file(db, LIVE)
    assert db.record_manual_metadata_file(LIVE) == 1


def test_a_same_named_file_in_another_album_is_left_alone(tmp_path):
    db = _db(tmp_path)
    _album_with_file(db, '/m/Radiohead/OK Computer/04 - Lucky.flac')
    assert db.record_manual_metadata_file(LIVE) == 0


def test_an_old_schema_without_the_table_reads_as_nothing_locked(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("DROP TABLE manual_metadata_files")
        conn.commit()
    assert db.manual_path_keys() == set()


def test_unlocking_forgets_the_albums_files(tmp_path):
    db = _db(tmp_path)
    album_id = _album_with_file(db, LIVE)
    db.record_manual_metadata_file(LIVE)
    assert db.clear_manual_lock(album_id) is True
    assert db.manual_path_keys() == set()


def test_unlocking_a_missing_album_says_so(tmp_path):
    assert _db(tmp_path).clear_manual_lock(424242) is False


def test_the_unlock_route(tmp_path, monkeypatch):
    import pytest
    web_server = pytest.importorskip('web_server')
    import api.artist_detail as artist_detail

    db = _db(tmp_path)
    album_id = _album_with_file(db, LIVE)
    db.record_manual_metadata_file(LIVE)
    monkeypatch.setattr(artist_detail, 'get_database', lambda: db)
    web_server.app.config['TESTING'] = True
    client = web_server.app.test_client()

    assert client.delete(f'/api/album/{album_id}/metadata-lock').get_json()['metadata_locked'] is False
    assert client.delete('/api/album/424242/metadata-lock').status_code == 404
