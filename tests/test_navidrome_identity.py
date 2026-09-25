from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.navidrome_client import NavidromeClient
from core.library.navidrome_identity import read_inventory, resolve_tracks, repair_rekeyed_tracks, IdentityError



def _old_db(path='/song'):
    """One Navidrome-mapped catalogue track, as the scan leaves it.

    The song id is not the row id here: it lives in lib2_media_server_mappings,
    with lib2_tracks.server_source/server_id as the compatibility projection.
    """
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, title TEXT, duration INTEGER,
                                 server_source TEXT, server_id TEXT, updated_at TEXT);
        CREATE TABLE lib2_track_files(id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT,
                                      is_primary INTEGER DEFAULT 1,
                                      file_state TEXT DEFAULT 'active');
        CREATE TABLE lib2_media_server_mappings(
            id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
            server_source TEXT NOT NULL, server_id TEXT NOT NULL, last_seen_at TEXT,
            UNIQUE(entity_type, entity_id, server_source),
            UNIQUE(entity_type, server_source, server_id));
    """)
    conn.execute("INSERT INTO lib2_tracks(id,title,duration,server_source,server_id)"
                 " VALUES(1,'Song',180000,'navidrome','old')")
    conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(1,?)", (path,))
    conn.execute("INSERT INTO lib2_media_server_mappings"
                 "(entity_type,entity_id,server_source,server_id)"
                 " VALUES('track',1,'navidrome','old')")
    conn.commit()
    return SimpleNamespace(_get_connection=lambda: conn)


def test_inventory_rejects_failed_page():
    client = SimpleNamespace(music_folder_id=None, _make_request=Mock(side_effect=[
        {'status': 'ok', 'scanStatus': {'scanning': False}},
        {'status': 'ok', 'searchResult3': {'song': [{'id': 'a', 'path': '/a'}]}},
        None,
    ]))
    with pytest.raises(IdentityError):
        read_inventory(client, page_size=1)


def test_inventory_rejects_scanning():
    client = SimpleNamespace(_make_request=Mock(return_value={'status': 'ok', 'scanStatus': {'scanning': True}}))
    with pytest.raises(IdentityError):
        read_inventory(client)
    assert client._make_request.call_count == 1


def test_resolve_stale_id_by_unique_exact_path():
    db = _old_db('/music/song.opus')
    result = resolve_tracks([SimpleNamespace(ratingKey='old')], {'new': {'id': 'new', 'title': 'Song', 'path': '/music/song.opus'}}, db)
    assert result[0].ratingKey == 'new'


@pytest.mark.parametrize('songs', [{}, {'a': {'id': 'a', 'path': '/song'}, 'b': {'id': 'b', 'path': '/song'}}])
def test_unresolved_or_ambiguous_aborts(songs):
    db = _old_db()
    with pytest.raises(IdentityError):
        resolve_tracks([SimpleNamespace(ratingKey='old')], songs, db)


def test_repair_repoints_the_mapping_and_the_manual_match(tmp_path):
    """A reissued id moves the MAPPING; the catalogue row never moves.

    Upstream merges two legacy `tracks` rows here and copies every enrichment
    column across. On the v2 catalogue the track keeps its identity, so nothing
    has to be preserved by hand -- the stale Navidrome id is re-pointed, the
    compatibility projection follows it, and the manual match (which stores the
    SERVER's id, see services/sync_service.py) is healed with it.
    """
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_library_track

    db = MusicDatabase(tmp_path / 'test.db')
    with db._get_connection() as c:
        track_id = seed_library_track(
            c, artist='Artist', album='Album', title='Song',
            track_server_id='old', file_path='/song', duration=180000,
            server_source='navidrome')
        c.execute("INSERT INTO lib2_media_server_mappings"
                  "(entity_type,entity_id,server_source,server_id)"
                  " VALUES('track',?,'navidrome','old')", (track_id,))
        c.commit()
    db.save_manual_library_match(1, 'spotify', 'source', 'old', server_source='navidrome')

    songs = {'new': {'id': 'new', 'title': 'Song', 'path': '/song'}}
    assert repair_rekeyed_tracks(db, songs) == 1
    with db._get_connection() as c:
        assert c.execute(
            "SELECT server_id FROM lib2_media_server_mappings"
            " WHERE entity_type='track' AND server_source='navidrome'").fetchone()[0] == 'new'
        assert c.execute("SELECT id, server_id FROM lib2_tracks").fetchone()[:] == (track_id, 'new')
        assert str(c.execute(
            "SELECT library_track_id FROM manual_library_track_matches").fetchone()[0]) == 'new'
    # idempotent: the id is live now, so there is nothing left to repair
    assert repair_rekeyed_tracks(db, songs) == 0


def test_repair_drops_a_stale_mapping_when_the_live_id_is_already_filed(tmp_path):
    """The sync can file the reissued id on a twin row before this runs.

    UNIQUE(entity_type, server_source, server_id) forbids a second claim on it,
    and folding two catalogue rows is core/library2/dedup_repair.py's job. The
    obsolete mapping goes; both catalogue rows stay exactly where they are.
    """
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_library_track

    db = MusicDatabase(tmp_path / 'test.db')
    with db._get_connection() as c:
        stale = seed_library_track(
            c, artist='Artist', album='Album', title='Song', artist_server_id='ar1',
            album_server_id='al1', track_server_id='old', file_path='/song',
            duration=180000, server_source='navidrome')
        twin = seed_library_track(
            c, artist='Artist', album='Album', title='Song', artist_server_id='ar1',
            album_server_id='al1', track_server_id='new', file_path='/song',
            duration=180000, server_source='navidrome')
        for track_id, server_id in ((stale, 'old'), (twin, 'new')):
            c.execute("INSERT INTO lib2_media_server_mappings"
                      "(entity_type,entity_id,server_source,server_id)"
                      " VALUES('track',?,'navidrome',?)", (track_id, server_id))
        c.commit()

    assert repair_rekeyed_tracks(db, {'new': {'id': 'new', 'title': 'Song', 'path': '/song'}}) == 1
    with db._get_connection() as c:
        assert [r[0] for r in c.execute(
            "SELECT server_id FROM lib2_media_server_mappings"
            " WHERE entity_type='track' AND server_source='navidrome'")] == ['new']
        assert c.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 2


def _server(monkeypatch, *, drop=False, inventory_failure=False):
    import database.music_database as database
    client = NavidromeClient.__new__(NavidromeClient)
    client.ensure_connection = lambda: True
    client.music_folder_id = None
    client.get_playlists_by_name = lambda name: [SimpleNamespace(id='pl')]
    db = _old_db()
    monkeypatch.setattr(database, 'get_database', lambda: db)
    current = ['new', 'extra']
    writes = []
    def request(endpoint, params=None, timeout=None):
        if endpoint == 'getScanStatus':
            return {'scanStatus': {'scanning': False, 'count': 2}}
        if endpoint == 'search3':
            if inventory_failure:
                return None
            return {'searchResult3': {'song': [] if params['songOffset'] else [
                {'id': 'new', 'title': 'Song', 'path': '/song'}, {'id': 'extra', 'title': 'Extra', 'path': '/extra'}]}}
        if endpoint == 'getPlaylist':
            return {'playlist': {'entry': [{'id': sid} for sid in current]}}
        if endpoint == 'createPlaylist':
            writes.append((endpoint, params))
            current[:] = [] if drop else params['songId']
            return {'status': 'ok'}
        if endpoint == 'updatePlaylist':
            writes.append((endpoint, params))
            for index in params.get('songIndexToRemove', []):
                current.pop(index)
            current.extend(params.get('songIdToAdd', []))
            return {'status': 'ok'}
        raise AssertionError(endpoint)
    client._make_request = request
    from core.navidrome_client import NavidromeTrack
    client.get_playlist_tracks = lambda pid: [NavidromeTrack({'id': sid}, client) for sid in current]
    return client, current, writes


def test_replace_resolves_stale_ids_and_checks_actual_contents(monkeypatch):
    client, current, writes = _server(monkeypatch)
    assert client.create_playlist(name='P', tracks=[SimpleNamespace(ratingKey='old')], playlist_id='pl')
    assert current == ['new']
    assert writes[0][1]['songId'] == ['new']


@pytest.mark.parametrize('method', ['create_playlist', 'update_playlist', 'reconcile_playlist', 'append_to_playlist'])
def test_failed_inventory_never_writes(monkeypatch, method):
    client, current, writes = _server(monkeypatch, inventory_failure=True)
    assert not getattr(client, method)('P', [SimpleNamespace(ratingKey='old')])
    assert not writes
    assert current == ['new', 'extra']


def test_success_response_with_missing_tracks_is_failure(monkeypatch):
    client, _, writes = _server(monkeypatch, drop=True)
    assert not client.create_playlist('P', [SimpleNamespace(ratingKey='old')], playlist_id='pl')
    assert len(writes) == 1


def test_reconcile_uses_recovered_id_before_planning_removals(monkeypatch):
    client, current, writes = _server(monkeypatch)
    assert client.reconcile_playlist('P', [SimpleNamespace(ratingKey='old')])
    assert current == ['new']
    assert writes[0][1]['songIndexToRemove'] == [1]


def test_append_keeps_existing_tracks_and_deduplicates_recovered_id(monkeypatch):
    client, current, writes = _server(monkeypatch)
    assert client.append_to_playlist('P', [SimpleNamespace(ratingKey='old')])
    assert current == ['new', 'extra']
    assert not writes


def test_no_matches_cannot_empty_a_playlist(monkeypatch):
    client, current, writes = _server(monkeypatch)
    assert not client.reconcile_playlist('P', [])
    assert not writes
    assert current == ['new', 'extra']


@pytest.mark.parametrize('change', [{'title': 'Different song'}, {'title': 'Song', 'duration': 250}])
def test_same_path_different_recording_is_not_recovered(change):
    song = {'id': 'new', 'path': '/song', **change}
    with pytest.raises(IdentityError):
        resolve_tracks([SimpleNamespace(ratingKey='old')], {'new': song}, _old_db())


def test_inventory_continues_after_a_short_page():
    client = SimpleNamespace(_make_request=Mock(side_effect=[
        {'scanStatus': {'scanning': False, 'count': 2}},
        {'searchResult3': {'song': [{'id': 'a'}]}},
        {'searchResult3': {'song': [{'id': 'b'}]}},
        {'searchResult3': {}},
        {'scanStatus': {'scanning': False, 'count': 2}},
    ]))
    assert set(read_inventory(client)) == {'a', 'b'}
    assert client._make_request.call_args_list[2].args[1]['songOffset'] == 1


def test_failed_navidrome_reconcile_never_falls_back_to_replace():
    from services.sync_service import PlaylistSyncService
    client = NavidromeClient.__new__(NavidromeClient)
    client.reconcile_playlist = Mock(return_value=False)
    client.update_playlist = Mock()
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    assert not service._reconcile_or_replace(client, 'P', [])
    client.update_playlist.assert_not_called()


def test_healthy_resync_does_not_fetch_the_whole_library(monkeypatch):
    client, current, writes = _server(monkeypatch, inventory_failure=True)
    request = client._make_request
    calls = []
    def observed(endpoint, params=None, timeout=None):
        calls.append(endpoint)
        return request(endpoint, params, timeout)
    client._make_request = observed
    assert client.reconcile_playlist('P', [SimpleNamespace(ratingKey='new'), SimpleNamespace(ratingKey='extra')])
    assert 'search3' not in calls
    assert not writes


def test_reconcile_exception_does_not_trigger_replace():
    from services.sync_service import PlaylistSyncService
    client = NavidromeClient.__new__(NavidromeClient)
    client.reconcile_playlist = Mock(side_effect=RuntimeError('disconnected'))
    client.update_playlist = Mock()
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    assert not service._reconcile_or_replace(client, 'P', [])
    client.update_playlist.assert_not_called()
