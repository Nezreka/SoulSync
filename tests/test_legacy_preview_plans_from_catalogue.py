"""The artist page's reorganize preview must plan from the catalogue.

The library-v2 page and the queue runner both plan from the catalogue
(``core.library2.reorganize_bridge``). The artist page still calls the legacy
``/api/library/album/<id>/reorganize/preview`` route, which used to plan with the
old provider/tag planner, so the preview could show a destination the move would
never produce (#1945 follow-up).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-legacy-preview-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'legacy-preview.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def album():
    db = web_server.get_database()
    conn = sqlite3.connect(os.environ['DATABASE_PATH'])
    try:
        artist_pk = conn.execute(
            "INSERT INTO lib2_artists (name) VALUES ('Preview Artist')").lastrowid
        album_pk = conn.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title) VALUES (?, 'Preview Album')",
            (artist_pk,)).lastrowid
        track_pk = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title) VALUES (?, 'Preview Track')",
            (album_pk,)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
            (track_pk, f'/preview/{os.urandom(4).hex()}.flac'))
        conn.commit()
    finally:
        conn.close()
    return db, album_pk


def _login(client):
    """Admin, so the #1504 ownership guard stays out of the way here."""
    with client.session_transaction() as sess:
        sess['profile_id'] = 1


def test_preview_plans_through_the_catalogue(monkeypatch, client, album):
    _db, album_id = album
    _login(client)
    captured = {}

    def fake_plan(conn, lib2_album_id, **kwargs):
        captured['album_id'] = lib2_album_id
        captured.update(kwargs)
        return {'success': True, 'status': 'planned', 'tracks': [{'title': 'Preview Track'}]}

    def legacy_planner_ran(**_kwargs):
        raise AssertionError('the legacy tag/provider planner must not run')

    monkeypatch.setattr('core.library2.reorganize_plan.plan_album_reorganize', fake_plan)
    monkeypatch.setattr('core.library_reorganize.preview_album_reorganize', legacy_planner_ran)

    resp = client.post(f'/api/library/album/{album_id}/reorganize/preview',
                       json={'source': 'spotify', 'mode': 'tags'})

    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body['status'] == 'planned'
    assert captured['album_id'] == album_id
    # the cohort the catalogue planner needs, and nothing provider-shaped
    assert callable(captured['build_final_path_fn'])
    assert 'primary_source' not in captured
    assert 'metadata_source' not in captured


def test_preview_honours_the_owning_profiles_library_root(monkeypatch, client, album):
    """#1504: the destination is planned against the owner's root, not the
    configured default, so the catalogue swap must not lose the override."""
    _db, album_id = album
    _login(client)
    seen = {}

    def fake_plan(conn, lib2_album_id, **kwargs):
        seen['transfer_dir'] = kwargs.get('transfer_dir')
        return {'success': True, 'status': 'planned', 'tracks': []}

    monkeypatch.setattr('core.library2.reorganize_plan.plan_album_reorganize', fake_plan)
    monkeypatch.setattr(web_server, 'config_root_path', lambda *a, **k: '/configured/default')
    monkeypatch.setattr('core.imports.paths.library_root_for_profile', lambda *a, **k: '/owner/root')

    resp = client.post(f'/api/library/album/{album_id}/reorganize/preview', json={})

    assert resp.status_code == 200, resp.data
    assert seen['transfer_dir'] == '/owner/root'


def test_preview_404_for_unknown_album(client):
    _login(client)
    resp = client.post('/api/library/album/999999/reorganize/preview', json={})
    assert resp.status_code == 404, resp.data
    assert resp.get_json()['success'] is False
