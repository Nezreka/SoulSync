"""Download-origin provenance: the deriver + the library_history persistence.

Feature: the origin-history modal (watchlist page / sync page) lists which
downloads were triggered by a watchlist scan vs a playlist sync, and lets the
user delete them. The trigger is derived once at the import chokepoint and
stored on the library_history row.
"""

from __future__ import annotations

import json

from core.downloads.origin import derive_download_origin
from database.music_database import MusicDatabase


# ── deriver ──────────────────────────────────────────────────────────────────

def test_explicit_stamp_wins():
    ctx = {'track_info': {
        '_dl_origin': 'playlist', '_dl_origin_context': 'Discover Weekly',
        'source_info': {'watchlist_artist_name': 'Drake'},  # would say watchlist
    }}
    assert derive_download_origin(ctx) == ('playlist', 'Discover Weekly')


def test_watchlist_provenance_from_wishlist_source_info():
    # The exact shape watchlist_scanner writes into the wishlist row, which
    # rides into track_info when the wishlist worker downloads the item.
    ctx = {'track_info': {'source_info': {
        'watchlist_artist_name': 'Kendrick Lamar',
        'watchlist_artist_id': 'spot123',
        'album_name': 'GNX',
    }}}
    assert derive_download_origin(ctx) == ('watchlist', 'Kendrick Lamar')


def test_playlist_provenance_from_source_info_and_json_string():
    ctx = {'track_info': {'source_info': {'playlist_name': 'Release Radar'}}}
    assert derive_download_origin(ctx) == ('playlist', 'Release Radar')
    # source_info sometimes survives as a JSON string — parse it.
    ctx2 = {'track_info': {'source_info': json.dumps({'playlist_name': 'RapCaviar'})}}
    assert derive_download_origin(ctx2) == ('playlist', 'RapCaviar')


def test_playlist_folder_mode_thread():
    ctx = {'track_info': {'_playlist_name': 'Today’s Top Hits'}}
    assert derive_download_origin(ctx) == ('playlist', 'Today’s Top Hits')


def test_manual_and_garbage_derive_none():
    assert derive_download_origin({'track_info': {'name': 'Song'}}) == (None, '')
    assert derive_download_origin({}) == (None, '')
    assert derive_download_origin({'track_info': 'not-a-dict'}) == (None, '')
    # invalid explicit origin is ignored, not trusted
    assert derive_download_origin({'track_info': {'_dl_origin': 'aliens'}}) == (None, '')


# ── persistence ──────────────────────────────────────────────────────────────

def _seed(db):
    db.add_library_history_entry(
        event_type='download', title='Squabble Up', artist_name='Kendrick Lamar',
        album_name='GNX', file_path='/music/k/squabble.flac',
        origin='watchlist', origin_context='Kendrick Lamar')
    db.add_library_history_entry(
        event_type='download', title='Opalite', artist_name='Taylor Swift',
        album_name='Showgirl', file_path='/music/t/opalite.flac',
        origin='playlist', origin_context='Release Radar')
    db.add_library_history_entry(  # manual download — no origin
        event_type='download', title='Random', artist_name='Someone',
        file_path='/music/r/random.flac')


def test_origin_entries_filtered_and_counted(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)

    wl, wl_total = db.get_download_origin_entries('watchlist')
    pl, pl_total = db.get_download_origin_entries('playlist')

    assert wl_total == 1 and wl[0]['title'] == 'Squabble Up'
    assert wl[0]['origin_context'] == 'Kendrick Lamar'
    assert pl_total == 1 and pl[0]['title'] == 'Opalite'
    assert pl[0]['origin_context'] == 'Release Radar'


def test_history_rows_fetch_and_delete(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    entries, _ = db.get_download_origin_entries('watchlist')
    ids = [e['id'] for e in entries]

    rows = db.get_library_history_rows_by_ids(ids)
    assert rows and rows[0]['file_path'] == '/music/k/squabble.flac'

    assert db.delete_library_history_rows(ids) == 1
    assert db.get_download_origin_entries('watchlist')[1] == 0
    # the other origin untouched
    assert db.get_download_origin_entries('playlist')[1] == 1


def test_delete_track_by_file_path(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('a1', 'A')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES ('al1', 'Al', 'a1')")
    cur.execute("""INSERT INTO tracks (id, album_id, artist_id, title, file_path)
                   VALUES ('t1', 'al1', 'a1', 'Song', '/music/k/squabble.flac')""")
    conn.commit()
    conn.close()

    assert db.delete_track_by_file_path('/music/k/squabble.flac') == 1
    assert db.delete_track_by_file_path('/music/k/squabble.flac') == 0
    assert db.delete_track_by_file_path('') == 0
