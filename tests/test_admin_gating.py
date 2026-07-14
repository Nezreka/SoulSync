"""Phase 3: server-side admin gating of shared/global-destructive endpoints.

The audit found these were callable by any profile (UI hid them, the API didn't).
For a real multi-user setup that's unsafe — a non-admin could restore/vacuum the
DB, wipe the shared library, clear the Plex library, or mint API keys. These
assert the @admin_only gate now blocks non-admins, that admin is NOT blocked
(zero change for single-profile installs, where everyone is the default admin),
and crucially that a PROFILE-SCOPED op (clearing your OWN wishlist) was NOT
over-gated.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-gate-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'gate.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


# (method, path) for every endpoint that must be admin-only.
GATED = [
    ('GET', '/api/v1/api-keys-internal'),
    ('POST', '/api/v1/api-keys-internal/generate'),
    ('DELETE', '/api/v1/api-keys-internal/revoke/abc'),
    ('POST', '/api/plex/clear-library'),
    ('PUT', '/api/library/clear-match'),
    ('DELETE', '/api/library/track/123'),
    ('DELETE', '/api/library/album/123'),
    ('POST', '/api/library/tracks/delete-batch'),
    ('POST', '/api/database/update'),
    ('POST', '/api/database/update/stop'),
    ('POST', '/api/database/backup'),
    ('DELETE', '/api/database/backups/x.db'),
    ('POST', '/api/database/backups/x.db/restore'),
    ('POST', '/api/database/maintenance/vacuum'),
    ('DELETE', '/api/metadata-cache/clear'),
    ('DELETE', '/api/metadata-cache/clear-musicbrainz'),
    ('POST', '/api/metadata-cache/evict'),
]


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


def _call(client, method, path):
    return client.open(path, method=method, json={})


@pytest.mark.parametrize('method,path', GATED)
def test_nonadmin_blocked(client, nonadmin, method, path):
    # @admin_only returns 403 BEFORE the view body runs, so this never triggers
    # the underlying destructive operation — safe to assert across all of them.
    assert _call(client, method, path).status_code == 403, f"{method} {path} should be 403 for non-admin"


def test_admin_not_blocked_by_the_gate(client):
    # Default session = profile 1 (admin). Prove the gate lets admin through on a
    # SAFE, read-only gated endpoint (listing API keys) — confirming the no-change
    # guarantee for single-profile installs without triggering a destructive op.
    assert client.get('/api/v1/api-keys-internal').status_code != 403


def test_profile_scoped_wishlist_clear_not_overgated(client, nonadmin):
    # Clearing your OWN wishlist is profile-scoped data — a non-admin MUST still
    # be allowed. This is the guard against a blanket sweep.
    assert _call(client, 'POST', '/api/wishlist/clear').status_code != 403


def test_nonadmin_cannot_attach_library_v2_context(client, nonadmin):
    response = client.post('/api/download', json={
        'username': 'user',
        'filename': 'folder/song.flac',
        'lib2_track_id': 1,
    })

    assert response.status_code == 403
    assert response.get_json()['error'] == 'Admin access required'


def test_normal_nonadmin_download_is_not_admin_gated(client, nonadmin):
    response = client.post('/api/download', json={'title': 'incomplete'})

    assert response.status_code == 400
    assert response.get_json()['error'] == 'Missing username or filename.'


def test_library_v2_profile_reaches_download_pipeline(client, monkeypatch):
    from core.library2.schema import ensure_library_v2_schema

    database = web_server.get_database()
    conn = database._get_connection()
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    # The lib2 FK triggers (audit P1-01) reject quality_profile_ids that
    # don't exist in the app-wide quality_profiles table — create the row
    # this test assigns instead of assuming bare ids pass.
    cur.execute(
        "INSERT OR IGNORE INTO quality_profiles(id, name) VALUES(7, 'Route Profile')")
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Route Artist')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, quality_profile_id) "
        "VALUES(?, 'Route Album', 7)", (artist_id,))
    album_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, quality_profile_id) "
        "VALUES(?, 'Route Track', 7)", (album_id,))
    track_id = cur.lastrowid
    conn.commit()
    conn.close()

    class _DownloadOrchestrator:
        @staticmethod
        def download(*_args):
            return object()

    monkeypatch.setattr(web_server, 'download_orchestrator', _DownloadOrchestrator())
    monkeypatch.setattr(web_server, 'run_async', lambda _result: 'download-id')
    monkeypatch.setattr(web_server, 'add_activity_item', lambda *_args: None)

    key = web_server._make_context_key('user', 'folder/song.flac')
    web_server.matched_downloads_context.pop(key, None)
    response = client.post('/api/download', json={
        'username': 'user',
        'filename': 'folder/song.flac',
        'title': 'Route Track',
        'artist': 'Route Artist',
        'album_name': 'Route Album',
        'quality_profile_id': 999,
        'lib2_track_id': track_id,
    })

    assert response.status_code == 200
    context = web_server.matched_downloads_context.pop(key)
    assert context['lib2_entity']['track_id'] == track_id
    assert context['track_info']['quality_profile_id'] == 7
    assert context['track_info']['name'] == 'Route Track'
    assert context['track_info']['artists'] == [{'name': 'Route Artist'}]


def test_admin_manual_download_without_lib2_context_is_correlated(client, monkeypatch):
    calls = []

    class _DownloadOrchestrator:
        @staticmethod
        def download(*_args):
            return object()

    def _capture(username, search_result, lib2_ctx, **kwargs):
        calls.append((username, search_result, lib2_ctx, kwargs))
        return {"download_id": "manual-shadow", "request_id": "arq1-shadow"}

    monkeypatch.setattr(web_server, 'download_orchestrator', _DownloadOrchestrator())
    monkeypatch.setattr(web_server, 'run_async', lambda _result: 'download-id')
    monkeypatch.setattr(web_server, 'add_activity_item', lambda *_args: None)
    monkeypatch.setattr(web_server, '_correlate_manual_grab', _capture)

    key = web_server._make_context_key('user', 'folder/shadow.flac')
    web_server.matched_downloads_context.pop(key, None)
    response = client.post('/api/download', json={
        'username': 'user',
        'filename': 'folder/shadow.flac',
        'title': 'Shadow Track',
        'artist': 'Shadow Artist',
        'album_name': 'Shadow Album',
    })

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][2] is None
    assert calls[0][1]['title'] == 'Shadow Track'
    context = web_server.matched_downloads_context.pop(key)
    assert context['_acquisition_grab_download_id'] == 'manual-shadow'
