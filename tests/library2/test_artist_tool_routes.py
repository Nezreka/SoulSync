"""The artist page's existing URLs must operate on the native catalogue."""
from contextlib import closing
import time
import threading
import ast
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.library2.test_api_routes import api


@pytest.fixture
def artist_tools(api):
    client, db, ids = api
    with closing(db._get_connection()) as conn:
        conn.execute('UPDATE lib2_artists SET legacy_artist_id=701 WHERE id=?', (ids['artist'],))
        conn.execute('UPDATE lib2_tracks SET legacy_track_id=901 WHERE id=?', (ids['album_track'],))
        conn.execute('UPDATE lib2_albums SET legacy_album_id=801 WHERE id=?', (ids['views'],))
        conn.commit()
    return client, db, ids


def wait_status(client, url):
    for _ in range(100):
        response = client.get(url)
        assert response.status_code == 200
        state = response.get_json()
        if state['status'] != 'running':
            return state
        time.sleep(.01)
    pytest.fail('artist-page job did not finish')


def test_old_tag_previews_read_native_metadata(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    monkeypatch.setattr('core.library2.retag.tag_preview', lambda contexts, **kw: [
        {'track_id': c['id'], 'title': c['title'], 'has_changes': True,
         'diff': [{'field': 'Title', 'db_value': c['title'], 'changed': True}]} for c in contexts])
    single = client.get('/api/library/track/901/tag-preview')
    assert single.status_code == 200
    assert single.get_json()['diff'][0]['db_value'] == 'One Dance'
    batch = client.post('/api/library/tracks/tag-preview-batch', json={'track_ids': [901]})
    assert batch.status_code == 200
    assert batch.get_json()['tracks'][0]['track_id'] == ids['album_track']


def test_artist_tool_status_routes_have_only_one_owner():
    # A standalone blueprint test cannot catch a stale web_server route that
    # wins Flask's dispatch order and reports an unrelated permanently-idle job.
    paths = []
    root = Path(__file__).resolve().parents[2]
    for name in ('web_server.py', 'api/library2_artist_tools.py'):
        for node in ast.walk(ast.parse((root / name).read_text())):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                    path = dec.args[0].value
                    if isinstance(path, str) and path.endswith('/status'):
                        paths.append(re.sub(r'<[^>]+>', '<id>', path))
    for path in ('/api/library/tracks/write-tags-batch/status',
                 '/api/library/tracks/analyze-replaygain-batch/status',
                 '/api/library/album/<id>/analyze-replaygain/status'):
        assert paths.count(path) == 1, f'{path} has competing handlers'


def test_old_source_info_resolves_the_legacy_reference_without_legacy_tables(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    monkeypatch.setattr('core.library2.source_info.track_source_info', lambda conn, tid: [{'track_id': tid}])
    response = client.get('/api/library/track/901/source-info')
    assert response.status_code == 200
    assert response.get_json()['downloads'] == [{'track_id': ids['album_track']}]
    assert client.get('/api/library/track/999/source-info').status_code == 404


def test_old_single_and_batch_writes_use_the_shared_native_writer(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    calls = []
    def write(database, tracks, **options):
        calls.append((tracks, options['embed_cover']))
        return {'written': len(tracks), 'failed': 0, 'skipped': 0, 'errors': []}
    monkeypatch.setattr('core.library2.retag.write_tags', write)
    response = client.post('/api/library/track/901/write-tags', json={'embed_cover': False})
    assert response.status_code == 200
    batch = client.post('/api/library/tracks/write-tags-batch', json={'track_ids': [901], 'embed_cover': True})
    assert batch.status_code == 200
    state = wait_status(client, '/api/library/tracks/write-tags-batch/status')
    assert state['written'] == 1
    assert calls == [([ids['album_track']], False), ([ids['album_track']], True)]


def test_old_replaygain_routes_use_native_tracks_and_albums(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    calls = []
    monkeypatch.setattr('core.replaygain.is_ffmpeg_available', lambda: True)
    def track(conn, tid, **kw):
        calls.append(('track', tid))
        return {'analyzed': True, 'track_gain_db': -2.5, 'error': None}
    def album(conn, aid, **kw):
        calls.append(('album', aid))
        return {'analyzed': 1, 'failed': 0, 'total': 1, 'errors': []}
    monkeypatch.setattr('core.library2.replaygain.analyze_track_replaygain', track)
    monkeypatch.setattr('core.library2.replaygain.analyze_album_replaygain', album)
    assert client.post('/api/library/track/901/analyze-replaygain').get_json()['success']
    assert client.post('/api/library/album/801/analyze-replaygain').status_code == 200
    assert wait_status(client, '/api/library/album/801/analyze-replaygain/status')['analyzed'] == 1
    assert client.post('/api/library/tracks/analyze-replaygain-batch', json={'track_ids': [901]}).status_code == 200
    assert wait_status(client, '/api/library/tracks/analyze-replaygain-batch/status')['analyzed'] == 1
    assert calls == [('track', ids['album_track']), ('album', ids['views']), ('track', ids['album_track'])]


def test_old_write_urls_keep_the_native_admin_guard(artist_tools):
    client, db, ids = artist_tools
    db.active_profile = 7
    assert client.post('/api/library/track/901/write-tags', json={}).status_code == 403
    assert client.post('/api/library/album/801/analyze-replaygain').status_code == 403


def test_old_write_urls_respect_the_migration_barrier(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    monkeypatch.setattr('core.library2.migration_gate.migration_required', lambda db: True)
    assert client.post('/api/library/track/901/write-tags', json={}).status_code == 503


def test_native_only_ids_are_explicit_and_never_guessed(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    monkeypatch.setattr('core.library2.source_info.track_source_info', lambda conn, tid: [{'track_id': tid}])
    assert client.get(f"/api/library/track/{ids['album_track']}/source-info").status_code == 404
    response = client.get(f"/api/library/track/lib2:{ids['album_track']}/source-info")
    assert response.status_code == 200
    assert response.get_json()['downloads'] == [{'track_id': ids['album_track']}]


def test_legacy_server_string_ids_are_preserved(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    with closing(db._get_connection()) as conn:
        conn.execute('UPDATE lib2_tracks SET legacy_track_id=? WHERE id=?',
                     ('server-track-uuid', ids['album_track']))
        conn.commit()
    monkeypatch.setattr('core.library2.source_info.track_source_info', lambda conn, tid: [{'track_id': tid}])
    response = client.get('/api/library/track/server-track-uuid/source-info')
    assert response.status_code == 200
    assert response.get_json()['downloads'] == [{'track_id': ids['album_track']}]


def test_old_artist_sync_keeps_a_native_only_library_safe(artist_tools):
    client, db, ids = artist_tools
    response = client.post('/api/library/artist/701/sync')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert data['artist_name'] == 'Drake'
    assert data['removal_skipped'] is True


def test_old_tag_write_and_native_retag_share_the_same_job_lock(artist_tools, monkeypatch):
    client, db, ids = artist_tools
    entered, release = threading.Event(), threading.Event()
    def write(*args, **kw):
        entered.set()
        assert release.wait(3)
        return {'written': 1, 'failed': 0, 'skipped': 0, 'errors': []}
    monkeypatch.setattr('core.library2.retag.write_tags', write)
    try:
        response = client.post('/api/library/tracks/write-tags-batch', json={'track_ids': [901]})
        assert response.status_code == 200
        assert entered.wait(1)
        monkeypatch.setattr('api.library_v2._RETAG_CONFLICT_WAIT_SECONDS', 0)
        native = client.post('/api/library/v2/tags/write', json={'track_ids': [ids['album_track']]})
        assert native.status_code == 409
        assert native.get_json()['job_id'] == response.get_json()['job_id']
    finally:
        release.set()
    assert wait_status(client, '/api/library/tracks/write-tags-batch/status')['written'] == 1


def test_old_tag_write_syncs_effective_metadata_to_the_mapped_server_id(artist_tools, monkeypatch):
    from flask import Flask
    from api.library2_artist_tools import register_artist_tool_routes
    from core.library2.job_registry import JobRegistry
    _client, db, ids = artist_tools
    with closing(db._get_connection()) as conn:
        for library, remote in [('', 'plex-track-98765'), ('own:7', 'private-plex-track')]:
            conn.execute('INSERT INTO lib2_media_server_mappings '
                         '(entity_type,entity_id,server_source,server_id,server_library_id) '
                         "VALUES('track',?,'plex',?,?)", (ids['album_track'], remote, library))
        conn.commit()
    captured = []
    def sync(rows, server):
        captured.extend(rows)
        assert server == 'plex'
        return {'synced': len(rows), 'failed': 0, 'errors': []}
    monkeypatch.setattr('core.library2.retag.write_tags', lambda *a, **kw:
                        {'written': 1, 'failed': 0, 'skipped': 0, 'errors': []})
    app = Flask(__name__)
    register_artist_tool_routes(app.route, get_database=lambda: db,
                               config_manager=SimpleNamespace(get_active_media_server=lambda: 'plex'),
                               registry=JobRegistry(), profile=lambda: 1,
                               hidden=lambda *a: False, sync_tracks_to_server=sync)
    response = app.test_client().post('/api/library/track/901/write-tags',
                                      json={'sync_to_server': True})
    assert response.get_json()['server_sync']['synced'] == 1
    assert captured[0]['id'] == 'plex-track-98765'
    assert captured[0]['title'] == 'One Dance'
    assert captured[0]['artist_name'] == 'Drake'
    assert len(captured) == 1
