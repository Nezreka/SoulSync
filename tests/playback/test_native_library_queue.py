"""Native playback uses temporary catalogues, with no provider or media calls."""
from contextlib import closing

import pytest
from flask import Flask

from tests.library2.test_api_routes import api  # noqa: F401


def test_artist_queue_includes_missing_siblings_without_fetching_discography(api):
    client, db, ids = api
    with closing(db._get_connection()) as conn:
        missing = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?, 'Missing', 2)",
            (ids['views'],),
        ).lastrowid
        conn.commit()
    data = client.get(f"/api/library/v2/artists/{ids['artist']}/play-queue").get_json()
    assert {row['track_id'] for row in data['files']} == {ids['album_track'], missing}
    row = next(row for row in data['files'] if row['track_id'] == missing)
    assert row['path'] == ''
    assert row['album_id'] == ids['views']
    assert data['pagination']['total_count'] == 2


def test_artist_queue_missing_and_owned_share_pagination(api):
    client, db, ids = api
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?, 'Missing', 2)",
                     (ids['views'],))
        conn.commit()
    url = f"/api/library/v2/artists/{ids['artist']}/play-queue?limit=1"
    pages = [client.get(url + f'&page={page}').get_json() for page in (1, 2)]
    assert all(page['pagination']['total_count'] == 2 for page in pages)
    assert pages[0]['files'][0]['track_id'] != pages[1]['files'][0]['track_id']


def test_consolidated_single_reuses_scoped_keeper_without_release_bindings(api):
    from core.playback.library_v2 import artist_queue_rows, resolve_native_queue_track
    _, db, ids = api
    row = resolve_native_queue_track(db, {'lib2_track_id': ids['single_track']},
                                    profile_id=1, library_owner_id=None, is_admin=True)
    assert row['file_path'] == '/m/one-dance.flac'
    with closing(db._get_connection()) as conn:
        conn.execute('UPDATE lib2_tracks SET monitored=1 WHERE id=?', (ids['single_track'],))
        tracks, total = artist_queue_rows(conn, ids['artist'])
    single = next(t for t in tracks if t['track_id'] == ids['single_track'])
    assert single['path'] == row['file_path']
    assert single['file_state'] == 'active'
    assert total == 2


def test_canonical_file_borrowing_respects_library_owner(api, monkeypatch):
    from core import library_scope
    from core.library2.recording_links import reference_owner
    _, db, ids = api
    monkeypatch.setattr(library_scope, 'SCOPE_PARKED', False)
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    with closing(db._get_connection()) as conn, library_scope.library_scope(7):
        assert reference_owner(conn, ids['single_track']) is None
        conn.execute('UPDATE lib2_track_files SET owner_profile_id=7 WHERE track_id=?', (ids['album_track'],))
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,'/other-library.flac',9)",
                     (ids['single_track'],))
        assert reference_owner(conn, ids['single_track'])['path'] == '/m/one-dance.flac'


def test_native_prefetch_resolves_ids_pin_and_profile_from_catalogue(api):
    from core.playback import library_v2
    _, db, ids = api
    with closing(db._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET spotify_id=NULL,external_ids=? WHERE id=?",
                     ('{"tidal":"track-42","deezer":"track-99"}', ids['ep_track']))
        conn.execute("UPDATE lib2_albums SET external_ids=?,canonical_source='tidal',"
                     "canonical_album_id='pinned-123',canonical_locked=1 WHERE id=?",
                     ('{"tidal":"wrong-456","deezer":"album-99"}', ids['ep']))
        from core.library2.editions import sync_default_edition
        sync_default_edition(conn, ids['ep'])
        conn.commit()
        profile = conn.execute("SELECT quality_profile_id FROM lib2_tracks WHERE id=?",
                               (ids['ep_track'],)).fetchone()[0]
    resolve = getattr(library_v2, 'resolve_native_queue_track', None)
    assert resolve is not None, 'native prefetch adapter is missing'
    row = resolve(db, {'lib2_track_id': ids['ep_track'], 'lib2_album_id': ids['ep'],
                      'title': 'Forged', 'artist': 'Forged', 'quality_profile_id': 999,
                      'source': 'spotify', 'source_track_id': 'wrong',
                      '_queue_request_id': 'row-1'},
                  profile_id=1, library_owner_id=None, is_admin=True)
    assert row['name'] == 'EP Song'
    assert row['source'] == 'tidal'
    assert row['id'] == row['source_track_id'] == 'track-42'
    assert row['album']['id'] == 'pinned-123'
    assert row['source_info']['album_provider_ids']['tidal'] == 'pinned-123'
    assert row['source_info']['lib2_track_id'] == ids['ep_track']
    assert row['source_info']['lib2_album_id'] == ids['ep']
    assert row['quality_profile_id'] == profile
    assert row['profile_id'] == 1
    assert row['library_owner_id'] is None
    assert row['_queue_request_id'] == 'row-1'
    assert row['_explicit_album_context']['id'] == 'pinned-123'
    assert row['release_edition_id'] is not None
    assert 'spotify_track_id' not in row
    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids['ep_track'],)).fetchone()[0] == 0


def test_native_prefetch_rejects_mismatched_album_and_shared_profile(api):
    from core.playback import library_v2
    _, db, ids = api
    resolve = getattr(library_v2, 'resolve_native_queue_track', None)
    assert resolve is not None, 'native prefetch adapter is missing'
    with pytest.raises(ValueError, match='entity'):
        resolve(db, {'lib2_track_id': ids['ep_track'], 'lib2_album_id': ids['views']},
                profile_id=1, library_owner_id=None, is_admin=True)
    with pytest.raises(PermissionError):
        resolve(db, {'lib2_track_id': ids['ep_track']},
                profile_id=7, library_owner_id=None, is_admin=False)


@pytest.fixture
def runtime(api, monkeypatch):
    from tests.playback.test_prefetch_runtime import web_server
    from core.runtime_state import download_batches, download_tasks, tasks_lock
    _, db, ids = api
    with tasks_lock:
        download_tasks.clear()
        download_batches.clear()
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    monkeypatch.setattr(web_server, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(web_server, '_selected_library_owner', lambda: None)
    monkeypatch.setattr(web_server, 'is_admin_request', lambda: True)
    monkeypatch.setattr(web_server, 'check_download_permission', lambda: None)
    monkeypatch.setattr(web_server.download_monitor, 'start_monitoring', lambda _: None)
    monkeypatch.setattr(web_server, '_start_next_batch_of_downloads', lambda _: None)
    monkeypatch.setattr(web_server, 'add_activity_item', lambda *args: None)
    yield web_server, db, ids, download_tasks, download_batches
    with tasks_lock:
        download_tasks.clear()
        download_batches.clear()


def _prefetch_client(server):
    app = Flask('native-prefetch')
    app.add_url_rule('/api/playback/queue/prefetch', view_func=server.playback_queue_prefetch,
                     methods=['POST'])
    return app.test_client()


def test_native_runtime_registers_exact_track_context_and_returns_identity(runtime):
    server, _, ids, tasks, batches = runtime
    response = _prefetch_client(server).post('/api/playback/queue/prefetch', json={'tracks': [{
        'lib2_track_id': ids['ep_track'], 'lib2_album_id': ids['ep'],
        'title': 'Forged', 'artist': 'Forged', 'quality_profile_id': 999,
        '_queue_request_id': 'native-1',
    }]})
    assert response.status_code == 200
    data = response.get_json()
    assert data['items'][0]['lib2_track_id'] == ids['ep_track']
    task = tasks[data['items'][0]['task_id']]
    assert task['track_info']['name'] == 'EP Song'
    assert task['track_info']['source_info']['lib2_track_id'] == ids['ep_track']
    assert task['profile_id'] == 1
    assert task['library_owner_id'] is None
    assert batches[task['batch_id']]['max_concurrent'] == 1


def test_native_runtime_forbids_shared_non_admin_acquisition(runtime, monkeypatch):
    server, _, ids, tasks, _ = runtime
    monkeypatch.setattr(server, 'is_admin_request', lambda: False)
    monkeypatch.setattr(server, 'get_current_profile_id', lambda: 7)
    response = _prefetch_client(server).post('/api/playback/queue/prefetch', json={'tracks': [{
        'lib2_track_id': ids['ep_track'], 'title': 'EP Song', 'artist': 'Drake',
    }]})
    assert response.status_code == 403
    assert not tasks


def test_prefetch_does_not_reuse_batches_between_library_owners(runtime, monkeypatch):
    server, _, _, tasks, batches = runtime
    # Boundary fixture prevents provider/search I/O; registration, ordering and
    # batch reuse run their real production implementations.
    monkeypatch.setattr(server, '_resolve_playback_prefetch_local_file', lambda _: None)
    first = server._start_playback_queue_prefetch([{'title': 'First', 'artist': 'A'}])
    monkeypatch.setattr(server, '_selected_library_owner', lambda: 7)
    second = server._start_playback_queue_prefetch([{'title': 'Second', 'artist': 'B'}])
    assert first['batch_ids'] != second['batch_ids']
    batch = batches[second['batch_ids'][0]]
    assert batch['library_owner_id'] == 7
    assert tasks[batch['queue'][0]]['library_owner_id'] == 7


def test_native_prefetch_never_reuses_quarantined_audio(api, tmp_path):
    from core.playback.library_v2 import resolve_native_queue_track
    _, db, ids = api
    path = tmp_path / 'quarantined.flac'
    path.write_bytes(b'quarantined fixture')
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(?,?,'quarantined')",
                     (ids['ep_track'], str(path)))
        conn.commit()
    row = resolve_native_queue_track(db, {'lib2_track_id': ids['ep_track']},
                                    profile_id=1, library_owner_id=None, is_admin=True)
    assert row['file_path'] == ''


def test_artist_queue_uses_only_the_selected_library(api, monkeypatch):
    from core import library_scope
    client, db, ids = api
    monkeypatch.setattr(library_scope, 'SCOPE_PARKED', False)
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?, '/own/ep.flac',7)",
                     (ids['ep_track'],))
        missing = conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?, 'Own missing',2)",
                               (ids['ep'],)).lastrowid
        conn.commit()
    token = library_scope.set_library_scope(7)
    try:
        data = client.get(f"/api/library/v2/artists/{ids['artist']}/play-queue").get_json()
        assert {row['track_id'] for row in data['files']} == {ids['ep_track'], missing}
        assert next(row for row in data['files'] if row['track_id'] == missing)['path'] == ''
    finally:
        library_scope.reset_library_scope(token)


@pytest.mark.parametrize('admin', [True, False])
def test_unmonitored_sibling_can_prefetch_into_an_owned_album(api, monkeypatch, admin):
    from core import library_scope
    from core.playback.library_v2 import resolve_native_queue_track
    _, db, ids = api
    monkeypatch.setattr(library_scope, 'SCOPE_PARKED', False)
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    monkeypatch.setattr(library_scope, 'library_scope_for_profile', lambda _: 7)
    monkeypatch.setattr(db, 'get_profile', lambda _: {'can_download': True}, raising=False)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?, '/own/ep.flac',7)", (ids['ep_track'],))
        missing = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?, 'Own missing')", (ids['ep'],)).lastrowid
        other_artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Other library artist')").lastrowid
        other_album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?, 'Other release')", (other_artist,)).lastrowid
        outside = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?, 'Other missing')", (other_album,)).lastrowid
        conn.commit()
    with library_scope.library_scope(7):
        result = resolve_native_queue_track(db, {'lib2_track_id': missing},
                                           profile_id=7, library_owner_id=7, is_admin=admin)
        assert result['lib2_track_id'] == missing
        with pytest.raises(PermissionError):
            resolve_native_queue_track(db, {'lib2_track_id': outside},
                                       profile_id=7, library_owner_id=7, is_admin=admin)


def test_native_completion_links_exact_original_track_and_polling_returns_it(runtime, tmp_path, monkeypatch):
    from core.library2 import autolink
    from core.quality.model import AudioQuality
    server, db, ids, tasks, batches = runtime
    data = server._start_playback_queue_prefetch([{'lib2_track_id': ids['ep_track'],
                                                 '_queue_request_id': 'native-complete'}])
    task = tasks[data['items'][0]['task_id']]
    path = tmp_path / 'completed.flac'
    path.write_bytes(b'completed fixture')
    monkeypatch.setattr('database.music_database.get_database', lambda: db)
    monkeypatch.setattr(autolink, '_warm_new_artwork', lambda *args: None)
    monkeypatch.setattr('core.imports.file_ops.probe_audio_quality', lambda _: AudioQuality('flac', bit_depth=16, sample_rate=44100))
    monkeypatch.setattr('core.library2.validation.refresh_imported_metadata', lambda *args: {})
    file_id = autolink.link_download_into_library_v2({
        '_final_processed_path': str(path), 'track_info': task['track_info'],
        'library_owner_id': None, 'username': 'fixture',
    }, raise_on_error=True)
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT track_id FROM lib2_track_files WHERE id=?', (file_id,)).fetchone()[0] == ids['ep_track']
        assert conn.execute('SELECT COUNT(*) FROM lib2_tracks').fetchone()[0] == 3
    task.update(status='completed', final_file_path=str(path))
    batches[task['batch_id']]['phase'] = 'complete'
    status = server._playback_queue_prefetch_status([task['batch_id']])
    returned = status['batches'][task['batch_id']]['tasks'][0]
    assert returned['track_info']['lib2_track_id'] == ids['ep_track']
    assert returned['track_info']['lib2_album_id'] == ids['ep']
    assert returned['final_file_path'] == str(path)


def test_admin_prefetch_acts_for_selected_owner(runtime, monkeypatch):
    from core import library_scope
    server, db, ids, tasks, batches = runtime
    monkeypatch.setattr(library_scope, 'SCOPE_PARKED', False)
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    monkeypatch.setattr(library_scope, 'session_scope', lambda: 7)
    monkeypatch.setattr(server, '_selected_library_owner', lambda: 7)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,7,1,'manual')",
                     (ids['ep_track'],))
        conn.commit()
    with library_scope.library_scope(7):
        result = server._start_playback_queue_prefetch([{'lib2_track_id': ids['ep_track']}])
        batch = batches[result['batch_ids'][0]]
        assert batch['profile_id'] == 7
        assert batch['library_owner_id'] == 7
        task = tasks[batch['queue'][0]]
        assert task['track_info']['profile_id'] == 7


def test_prefetch_status_does_not_expose_another_owners_batch(runtime, monkeypatch):
    server, _, _, _, _ = runtime
    monkeypatch.setattr(server, '_resolve_playback_prefetch_local_file', lambda _: None)
    first = server._start_playback_queue_prefetch([{'title': 'Other', 'artist': 'A'}])
    monkeypatch.setattr(server, '_selected_library_owner', lambda: 7)
    result = server._playback_queue_prefetch_status(first['batch_ids'])
    assert result['batches'] == {}


def test_native_queue_can_acquire_catalogue_tracks_without_junction_credits(runtime):
    server, db, ids, tasks, _ = runtime
    with closing(db._get_connection()) as conn:
        conn.execute('DELETE FROM lib2_track_artists WHERE track_id=?', (ids['ep_track'],))
        conn.commit()
    result = server._start_playback_queue_prefetch([{'lib2_track_id': ids['ep_track']}])
    row = tasks[result['items'][0]['task_id']]['track_info']
    assert row['artist'] == 'Drake'
    assert row['artists'] == [{'name': 'Drake'}]


def test_native_own_profile_acquires_only_into_its_library(runtime, monkeypatch):
    from core import library_scope
    server, db, ids, tasks, batches = runtime
    monkeypatch.setattr(library_scope, 'SCOPE_PARKED', False)
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    monkeypatch.setattr(library_scope, 'library_scope_for_profile', lambda _: 7)
    monkeypatch.setattr(db, 'get_profile', lambda _: {'can_download': True}, raising=False)
    monkeypatch.setattr(server, 'is_admin_request', lambda: False)
    monkeypatch.setattr(server, 'get_current_profile_id', lambda: 7)
    monkeypatch.setattr(server, '_selected_library_owner', lambda: 7)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,7,1,'manual')",
                     (ids['ep_track'],))
        conn.commit()
    with library_scope.library_scope(7):
        response = _prefetch_client(server).post('/api/playback/queue/prefetch', json={'tracks': [{
            'lib2_track_id': ids['ep_track'], 'library_owner_id': 9, 'profile_id': 9,
        }]})
    assert response.status_code == 200
    row = tasks[response.get_json()['items'][0]['task_id']]
    assert row['profile_id'] == row['library_owner_id'] == 7
    assert batches[row['batch_id']]['library_owner_id'] == 7
