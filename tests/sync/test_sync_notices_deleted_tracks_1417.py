"""#1417: a mirror sync never noticed a matched file had been deleted.

it skips when the track list is unchanged and everything matched last time,
and that fingerprint never looked at the library. after a deleted file every
sync said "unchanged": nothing re-matched, nothing wishlisted, and only
deleting and recreating the playlist brought the song back. the skip now also
needs every matched library track to still exist.
"""

from __future__ import annotations

import hashlib
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.sync.library_presence import matched_tracks_still_present


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Artist')")
        conn.execute("INSERT INTO albums (id, title, artist_id) VALUES ('al1', 'Album', 'a1')")
        conn.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path) "
                     "VALUES ('lib-1', 'Song', 'a1', 'al1', '/m/1.flac')")
        conn.commit()
    return db


def _delete_from_library(db, track_id='lib-1'):
    with db._get_connection() as conn:
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        conn.commit()


def row(extra):
    return {'extra_data': json.dumps(extra)}


def test_presence_is_proven_only_while_every_matched_track_exists(db):
    assert matched_tracks_still_present(db, [row({'in_library': True, 'library_track_id': 'lib-1'})])
    assert matched_tracks_still_present(db, [row({'in_library': False})])
    # synced before ids were kept: can't be checked, so it is not proven
    assert not matched_tracks_still_present(db, [row({'in_library': True})])
    _delete_from_library(db)
    assert not matched_tracks_still_present(db, [row({'in_library': True, 'library_track_id': 'lib-1'})])


def test_the_sync_records_which_library_track_it_matched(db):
    from core.discovery.sync import _record_library_membership

    pid = db.mirror_playlist('spotify', 'p1', 'Mix', [
        {'track_name': 'Song', 'artist_name': 'Artist', 'source_track_id': 'sp1'}], profile_id=1)
    track_row = db.get_mirrored_playlist_tracks(pid)[0]
    with patch('database.music_database.get_database', return_value=db):
        _record_library_membership(
            [{'db_track_id': track_row['id']}],
            [{'index': 0, 'status': 'found', 'matched_track': {'id': 'lib-1'}}])
    extra = json.loads(db.get_mirrored_playlist_tracks(pid)[0]['extra_data'])
    assert extra['in_library'] is True and extra['library_track_id'] == 'lib-1'


def _mirror(db):
    pid = db.mirror_playlist('spotify', 'p1', 'Mix', [
        {'track_name': 'Song', 'artist_name': 'Artist', 'source_track_id': 'sp1'}], profile_id=1)
    track_row = db.get_mirrored_playlist_tracks(pid)[0]
    db.update_mirrored_track_extra_data(track_row['id'], {
        'discovered': True,
        'matched_data': {'id': 'sp1', 'name': 'Song', 'artists': [{'name': 'Artist'}],
                         'album': {'name': 'Album'}},
        'in_library': True, 'library_track_id': 'lib-1'})
    return pid


def _sync(db, pid):
    """the real handler, with a status that says: same tracks, all matched."""
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    pl = db.get_mirrored_playlist(pid)
    status = {f'auto_mirror_{pid}': {
        'tracks_hash': hashlib.md5('sp1'.encode()).hexdigest(),
        'mirror_tracks_hash': hashlib.md5('sp1'.encode()).hexdigest(),
        'matched_tracks': 1,
        'quality_profile_id': pl.get('quality_profile_id'),
    }}
    started, calls = threading.Event(), []

    def _run_sync_task(*a, **k):
        calls.append(a)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = status
    deps.config_manager.get_active_media_server.return_value = 'navidrome'
    result = auto_sync_playlist({'playlist_id': str(pid)}, deps)
    if result.get('status') == 'started':
        assert started.wait(5)
    return result, calls


def test_an_unchanged_playlist_with_its_tracks_in_place_still_skips(db):
    result, calls = _sync(db, _mirror(db))
    assert result['status'] == 'skipped' and calls == []


def test_a_deleted_track_makes_the_next_sync_run(db):
    pid = _mirror(db)
    _delete_from_library(db)
    result, calls = _sync(db, pid)
    assert result['status'] == 'started' and len(calls) == 1


def test_the_sync_result_names_the_library_track_each_song_matched():
    """the id the recorder stores comes from here, through the real sync."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from services.sync_service import PlaylistSyncService

    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    lib = SimpleNamespace(ratingKey='lib-1', title='Song', artist='Artist', album='Album')
    service._find_track_in_media_server = AsyncMock(side_effect=[(lib, 1.0), (None, 0.0)])
    client = Mock()
    client.is_connected.return_value = True
    client.update_playlist.return_value = True
    service._get_active_media_client = lambda: (client, 'navidrome')
    service._wishlist_unmatched = Mock(return_value=0)
    src = [SimpleNamespace(id=s, name=n, artists=['Artist'], album='Album', duration_ms=1)
           for s, n in (('1', 'Song'), ('2', 'Gone'))]
    result = asyncio.run(service.sync_playlist(SimpleNamespace(id='pl', name='Mix', tracks=src)))
    by_id = {d['source_track_id']: d for d in result.match_details}
    assert by_id['1']['matched_track']['id'] == 'lib-1'
    assert by_id['2']['matched_track'] is None
