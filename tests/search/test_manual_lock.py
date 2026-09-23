"""the hand-tagged lock: a file the user tagged themselves marks its track and
album so enrichment never rematches it to the studio release, including after
a media server rescans it into a brand new row."""

from __future__ import annotations

from pathlib import Path

from database.music_database import MusicDatabase


class _ScannedTrack:
    """what the media-server scanner hands insert_or_update_media_track"""

    def __init__(self, track_id, title, file_path):
        self.ratingKey = track_id
        self.title = title
        self.trackNumber = 1
        self.duration = 250_000
        self.path = file_path
        self.bitRate = 1411
        self._data = {'ArtistItems': [{'Name': 'Radiohead'}], 'AlbumArtists': [{'Name': 'Radiohead'}]}


def _db(tmp_path: Path) -> MusicDatabase:
    db = MusicDatabase(database_path=str(tmp_path / 'lib.db'))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('ar', 'Radiohead', 'jellyfin')")
    conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al', 'ar', 'Live at Glastonbury 2003', 'jellyfin')")
    conn.commit()
    conn.close()
    return db


def _row(db, table, row_id):
    conn = db._get_connection()
    conn.row_factory = None
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
    values = cur.fetchone()
    conn.close()
    return dict(zip(cols, values)) if values else None


def test_the_path_key_is_the_part_every_mount_agrees_on():
    key = MusicDatabase.manual_path_key
    assert key('/app/Transfer/Radiohead/Live 2003/01 - Lucky.flac') == 'radiohead/live 2003/01 - lucky.flac'
    assert key(r'D:\Music\Radiohead\Live 2003\01 - Lucky.flac') == 'radiohead/live 2003/01 - lucky.flac'
    assert key('') == ''


def test_a_rescanned_file_is_locked_even_on_a_fresh_row(tmp_path):
    db = _db(tmp_path)
    # SoulSync wrote it under its own mount...
    db.record_manual_metadata_file('/app/Transfer/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac',
                                   album_title='Live at Glastonbury 2003', album_artist='Radiohead')
    # ...and the media server finds it under its own
    db.insert_or_update_media_track(
        _ScannedTrack('tr-new', 'Lucky', '/media/music/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac'),
        album_id='al', artist_id='ar', server_source='jellyfin')

    track = _row(db, 'tracks', 'tr-new')
    album = _row(db, 'albums', 'al')
    assert track['metadata_locked'] == 1 and album['metadata_locked'] == 1
    # every enrichment worker only picks NULL / not_found / error rows
    statuses = [v for k, v in track.items() if k.endswith('_match_status')]
    assert statuses and all(v == 'manual' for v in statuses)
    assert all(v == 'manual' for k, v in album.items() if k.endswith('_match_status'))


def test_a_file_already_in_the_library_is_locked_right_away(tmp_path):
    db = _db(tmp_path)
    db.insert_or_update_media_track(
        _ScannedTrack('tr-1', 'Lucky', '/m/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac'),
        album_id='al', artist_id='ar', server_source='jellyfin')
    assert _row(db, 'tracks', 'tr-1')['metadata_locked'] in (0, None)

    locked = db.record_manual_metadata_file('/other/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac')
    assert locked == 1
    assert _row(db, 'tracks', 'tr-1')['metadata_locked'] == 1


def test_an_ordinary_file_is_left_alone(tmp_path):
    db = _db(tmp_path)
    db.record_manual_metadata_file('/app/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac')
    db.insert_or_update_media_track(
        _ScannedTrack('tr-2', 'Airbag', '/m/Radiohead/OK Computer/01 - Airbag.flac'),
        album_id='al', artist_id='ar', server_source='jellyfin')
    track = _row(db, 'tracks', 'tr-2')
    assert track['metadata_locked'] in (0, None)
    assert not any(v == 'manual' for k, v in track.items() if k.endswith('_match_status'))


def test_a_same_named_file_in_another_album_is_left_alone(tmp_path):
    db = _db(tmp_path)
    db.record_manual_metadata_file('/app/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac')
    db.insert_or_update_media_track(
        _ScannedTrack('tr-3', 'Lucky', '/m/Radiohead/OK Computer/04 - Lucky.flac'),
        album_id='al', artist_id='ar', server_source='jellyfin')
    assert _row(db, 'tracks', 'tr-3')['metadata_locked'] in (0, None)


def test_an_old_schema_without_the_table_still_scans(tmp_path):
    db = _db(tmp_path)
    conn = db._get_connection()
    conn.execute("DROP TABLE manual_metadata_files")
    conn.commit()
    conn.close()
    assert db.insert_or_update_media_track(
        _ScannedTrack('tr-4', 'Lucky', '/m/a/b/c.flac'), album_id='al', artist_id='ar', server_source='jellyfin')
    assert _row(db, 'tracks', 'tr-4') is not None


def test_unlocking_hands_the_album_back_and_stops_the_relock(tmp_path):
    db = _db(tmp_path)
    path = '/m/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac'
    db.record_manual_metadata_file(path)
    db.insert_or_update_media_track(_ScannedTrack('tr-u', 'Lucky', path),
                                    album_id='al', artist_id='ar', server_source='jellyfin')
    assert _row(db, 'albums', 'al')['metadata_locked'] == 1

    assert db.clear_manual_lock('al') is True
    track, album = _row(db, 'tracks', 'tr-u'), _row(db, 'albums', 'al')
    assert track['metadata_locked'] == 0 and album['metadata_locked'] == 0
    # 'manual' goes back to NULL, so every enrichment worker picks it up again
    assert all(v is None for k, v in track.items() if k.endswith('_match_status'))

    # a rescan doesn't lock it again
    db.insert_or_update_media_track(_ScannedTrack('tr-u', 'Lucky', path),
                                    album_id='al', artist_id='ar', server_source='jellyfin')
    assert _row(db, 'tracks', 'tr-u')['metadata_locked'] == 0


def test_unlocking_a_missing_album_says_so(tmp_path):
    assert _db(tmp_path).clear_manual_lock('nope') is False


def test_the_unlock_route(tmp_path, monkeypatch):
    import pytest
    web_server = pytest.importorskip('web_server')
    import api.artist_detail as artist_detail

    db = _db(tmp_path)
    db.record_manual_metadata_file('/m/Radiohead/Live at Glastonbury 2003/04 - Lucky.flac')
    monkeypatch.setattr(artist_detail, 'get_database', lambda: db)
    web_server.app.config['TESTING'] = True
    client = web_server.app.test_client()

    assert client.delete('/api/album/al/metadata-lock').get_json()['metadata_locked'] is False
    assert client.delete('/api/album/missing/metadata-lock').status_code == 404
