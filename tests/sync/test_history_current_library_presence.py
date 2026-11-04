"""Historic misses must not advertise downloads for recordings now owned."""
import copy

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track


@pytest.mark.parametrize('server', ['navidrome', 'plex', 'jellyfin'])
@pytest.mark.parametrize('recognized', [True, False])
def test_history_exposes_current_presence_without_rewriting_run(tmp_path, server, recognized):
    from core.sync.library_presence import refresh_history_library_presence

    db = MusicDatabase(str(tmp_path / 'history.db'))
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Melodysheep', album='One Species',
                                 title='One Species', server_source=server,
                                 track_server_id='current-song', file_path='/music/one.flac')
        conn.execute('UPDATE lib2_tracks SET spotify_id=? WHERE id=?', ('sp-one', tid))
        if not recognized:
            conn.execute('UPDATE lib2_tracks SET server_id=NULL,server_source=NULL WHERE id=?', (tid,))
    historic = {'index': 0, 'name': 'One Species', 'artist': 'Melodysheep',
                'source_track_id': 'sp-one', 'status': 'not_found', 'download_status': 'wishlist'}
    entry = {'profile_id': 1, 'tracks_found': 0, 'track_results': [copy.deepcopy(historic)]}
    refresh_history_library_presence(db, entry, server)
    detail = entry['track_results'][0]
    assert {key: detail[key] for key in historic} == historic
    assert detail['current_library_match']['catalogue_id'] == tid
    assert detail['current_library_match']['server_id'] == ('current-song' if recognized else None)
    assert entry['current_tracks_in_library'] == 1
    assert entry['current_tracks_recognized'] == int(recognized)
    assert entry['tracks_found'] == 0


@pytest.mark.parametrize('reason', ['deleted', 'wrong_artist', 'ambiguous_id', 'other_server'])
def test_current_presence_rejects_invalid_library_evidence(tmp_path, reason):
    from core.sync.library_presence import refresh_history_library_presence

    db = MusicDatabase(str(tmp_path / 'invalid.db'))
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Other' if reason == 'wrong_artist' else 'Melodysheep',
                                 album='One Species', title='One Species', server_source='plex',
                                 file_path='/music/one.flac')
        if reason != 'wrong_artist':
            conn.execute('UPDATE lib2_tracks SET spotify_id=? WHERE id=?', ('sp-one', tid))
        if reason == 'deleted':
            conn.execute("UPDATE lib2_track_files SET file_state='deleted'")
        if reason == 'ambiguous_id':
            duplicate = seed_library_track(conn, artist='Other', album='Other', title='Different',
                                           artist_server_id='ar2', album_server_id='al2',
                                           track_server_id='tr2', file_path='/music/two.flac')
            conn.execute('UPDATE lib2_tracks SET spotify_id=? WHERE id=?', ('sp-one', duplicate))
    entry = {'profile_id': 1, 'track_results': [{'name': 'One Species', 'artist': 'Melodysheep',
                                               'source_track_id': 'sp-one', 'status': 'not_found'}]}
    refresh_history_library_presence(db, entry, 'navidrome' if reason == 'other_server' else 'plex')
    if reason == 'other_server':
        assert entry['current_tracks_in_library'] == 1
        assert entry['current_tracks_recognized'] == 0
    else:
        assert entry['track_results'][0]['current_library_match'] is None
        assert entry['current_tracks_in_library'] == 0


def test_history_endpoint_refreshes_one_species_without_persisting_history(tmp_path, monkeypatch):
    web_server = pytest.importorskip('web_server')
    db = MusicDatabase(str(tmp_path / 'api.db'))
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Melodysheep', album='One Species', title='One Species',
                           server_source='navidrome', file_path='/music/one.flac')
    historic = {'profile_id': 1, 'playlist_name': 'Verträumt', 'tracks_found': 0,
                'track_results': '[{"name":"One Species","artist":"Melodysheep","status":"not_found"}]'}
    monkeypatch.setattr(db, 'get_sync_history_entry', lambda *a, **kw: copy.deepcopy(historic))
    monkeypatch.setattr(web_server, 'MusicDatabase', lambda: db)
    monkeypatch.setattr(web_server, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')
    response = web_server.app.test_client().get('/api/sync/history/6')
    assert response.status_code == 200
    entry = response.get_json()['entry']
    assert entry['track_results'][0]['current_library_match']['server_id'] == 'tr1'
    assert entry['track_results'][0]['status'] == 'not_found'
    assert historic['tracks_found'] == 0


def test_provider_ids_do_not_collide_between_services(tmp_path):
    from core.sync.library_presence import refresh_history_library_presence
    db = MusicDatabase(str(tmp_path / 'providers.db'))
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Wrong Artist', album='Wrong', title='Wrong Song',
                                 server_source='navidrome', file_path='/music/wrong.flac')
        conn.execute('UPDATE lib2_tracks SET external_ids=? WHERE id=?', ('{"itunes":"123"}', tid))
    entry = {'source': 'deezer', 'profile_id': 1,
             'track_results': [{'name': 'One Species', 'artist': 'Melodysheep', 'source_track_id': '123'}]}
    refresh_history_library_presence(db, entry, 'navidrome')
    assert entry['current_tracks_in_library'] == 0


def test_shared_file_does_not_borrow_another_librarys_mapping(tmp_path):
    from core.sync.library_presence import refresh_history_library_presence
    from core.library2.media_mappings import upsert_mapping
    db = MusicDatabase(str(tmp_path / 'mapping_scope.db'))
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Melodysheep', album='One Species', title='One Species',
                                 server_source='soulsync', file_path='/music/one.flac')
        upsert_mapping(conn.cursor(), 'track', tid, 'jellyfin', 'other-library-song',
                       server_library_id='own:2', compat=False)
    entry = {'profile_id': 1, 'track_results': [{'name': 'One Species', 'artist': 'Melodysheep'}]}
    refresh_history_library_presence(db, entry, 'jellyfin')
    assert entry['current_tracks_in_library'] == 1
    assert entry['current_tracks_recognized'] == 0


@pytest.mark.parametrize('owner', [2, 3])
def test_own_library_presence_keeps_file_and_mapping_scopes_together(tmp_path, monkeypatch, owner):
    from core.sync.library_presence import refresh_history_library_presence
    from core.library2.media_mappings import upsert_mapping
    monkeypatch.setattr('core.library_scope.library_scope_for_profile', lambda profile: 2)
    monkeypatch.setattr('core.library2.sql_util.separated', lambda scope: scope)
    db = MusicDatabase(str(tmp_path / 'own.db'))
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Melodysheep', album='One Species', title='One Species',
                                 server_source='jellyfin', file_path='/music/one.flac')
        conn.execute('UPDATE lib2_track_files SET owner_profile_id=?', (owner,))
        upsert_mapping(conn.cursor(), 'track', tid, 'jellyfin', 'shared-song', compat=False)
    entry = {'profile_id': 2, 'track_results': [{'name': 'One Species', 'artist': 'Melodysheep'}]}
    refresh_history_library_presence(db, entry, 'jellyfin')
    assert entry['current_tracks_in_library'] == int(owner == 2)
    assert entry['current_tracks_recognized'] == 0
