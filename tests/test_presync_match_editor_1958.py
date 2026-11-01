"""#1958: the editor for a mirrored playlist that hasn't synced yet.

there is no server playlist to compare against, but there is the library track
each song will sync as (the background library check stamps it). the pre-sync
editor shows that, and a pick is stored the way the compare editor stores a
swap, so the first sync uses it. heavy (imports web_server), its own module.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-presync-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'presync.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def db():
    return web_server.get_database()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'plex')
    return web_server.app.test_client()


def _as(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


def _library(db, *titles):
    """a library track per title, on plex as srv-<n>. returns the server ids."""
    tag = os.urandom(3).hex()
    conn = db._get_connection()
    try:
        artist = conn.execute("INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
                              (f'Cosmic Quest {tag}', f'cosmic quest {tag}')).lastrowid
        album = conn.execute("INSERT INTO lib2_albums (primary_artist_id, title) VALUES (?, ?)",
                             (artist, 'Without You')).lastrowid
        ids = []
        for n, title in enumerate(titles):
            sid = f'srv-{tag}-{n}'
            conn.execute("INSERT INTO lib2_tracks (album_id, title, duration, server_source, server_id) "
                         "VALUES (?, ?, 184000, 'plex', ?)", (album, title, sid))
            ids.append(sid)
        conn.commit()
    finally:
        conn.close()
    return ids


def _mirror(db, owner=1):
    tag = os.urandom(3).hex()
    pk = db.mirror_playlist(
        source='spotify', source_playlist_id=f'p-{tag}', name=f'Mix {tag}',
        tracks=[{'track_name': 'Without You', 'artist_name': 'Cosmic Quest', 'source_track_id': f'sp1-{tag}'},
                {'track_name': 'Rare Cut', 'artist_name': 'Nobody', 'source_track_id': f'sp2-{tag}'}],
        profile_id=owner)
    rows = db.get_mirrored_playlist_tracks(pk)
    return pk, rows


def _stamp(db, row, library_id):
    db.update_mirrored_track_extra_data(row['id'], {
        'in_library': bool(library_id), 'library_track_id': library_id,
        'library_checked_at': 1, 'library_checked_via': 'check'})


def _view(client, pk):
    r = client.get(f'/api/mirrored-playlists/{pk}/presync')
    assert r.status_code == 200, r.data
    return r.get_json()


def test_each_song_shows_the_library_track_it_will_sync_as(client, db):
    radio, _extended = _library(db, 'Without You (Radio Edit)', 'Without You (Extended Mix)')
    pk, rows = _mirror(db)
    _stamp(db, rows[0], radio)
    _stamp(db, rows[1], None)
    _as(client, 1)
    view = _view(client, pk)
    assert view['unchecked'] == 0
    first, second = view['tracks']
    assert first['match_status'] == 'matched'
    assert first['server_track']['title'] == 'Without You (Radio Edit)'
    assert first['server_track']['duration'] == 184000
    assert second['match_status'] == 'missing' and second['server_track'] is None


def test_picking_a_version_is_what_the_sync_will_use(client, db):
    radio, extended = _library(db, 'Without You (Radio Edit)', 'Without You (Extended Mix)')
    pk, rows = _mirror(db)
    _stamp(db, rows[0], radio)
    _as(client, 1)
    r = client.post(f'/api/mirrored-playlists/{pk}/presync/match',
                    json={'mirror_track_id': rows[0]['id'], 'track_id': extended,
                          'track_title': 'Without You (Extended Mix)'})
    assert r.get_json()['success'] is True
    view = _view(client, pk)
    assert view['tracks'][0]['server_track']['title'] == 'Without You (Extended Mix)'
    assert view['tracks'][0]['has_manual_match'] is True
    # the same durable match the compare editor stores, which the sync reads
    source_id = rows[0]['source_track_id']
    assert db.find_manual_library_matches_bulk(1, [source_id], 'plex').get(source_id)


def test_not_this_one_sends_the_song_to_download_and_never_rematches(client, db):
    radio, = _library(db, 'Without You (Radio Edit)')
    pk, rows = _mirror(db)
    _stamp(db, rows[0], radio)
    _as(client, 1)
    r = client.post(f'/api/mirrored-playlists/{pk}/presync/unmatch',
                    json={'mirror_track_id': rows[0]['id']})
    assert r.get_json()['success'] is True
    assert _view(client, pk)['tracks'][0]['match_status'] == 'missing'
    source_id = rows[0]['source_track_id']
    assert (source_id, radio) in db.get_server_match_rejections(1, 'plex', [source_id])


def test_someone_elses_mirror_is_not_there(client, db):
    pk, rows = _mirror(db, owner=1)
    other = db.create_profile(name=f'other_{os.urandom(3).hex()}')
    _as(client, other)
    assert client.get(f'/api/mirrored-playlists/{pk}/presync').status_code == 404
    assert client.post(f'/api/mirrored-playlists/{pk}/presync/match',
                       json={'mirror_track_id': rows[0]['id'], 'track_id': 'x'}).status_code == 404
    assert json.loads(db.get_mirrored_playlist_tracks(pk)[0].get('extra_data') or '{}') == {}
