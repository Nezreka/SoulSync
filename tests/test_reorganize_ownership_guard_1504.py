"""Ownership guard on the reorganize endpoints (#1504).

A non-admin profile may only preview/apply reorganize for albums they own.
Admins may reorganize anything. Anything else gets a 403.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-reorg-guard-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'reorg-guard.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')
import core.library_reorganize as lr
import core.reorganize_queue as rq


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def profiles():
    db = web_server.get_database()
    sam = db.create_profile(name=f'sam_reorg_{os.urandom(3).hex()}')
    alex = db.create_profile(name=f'alex_reorg_{os.urandom(3).hex()}')
    # a real album row so the enqueue endpoint gets past its 404 check
    # (neither artists.id nor albums.id auto-increments — set both explicitly)
    artist_pk = f'guard_artist_{os.urandom(4).hex()}'
    album_pk = f'guard_album_{os.urandom(4).hex()}'
    conn = sqlite3.connect(os.environ['DATABASE_PATH'])
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES (?, 'Guard Artist')", (artist_pk,))
        conn.execute(
            "INSERT INTO albums (id, title, artist_id, owner_profile_id) VALUES (?, 'Guard Album', ?, ?)",
            (album_pk, artist_pk, sam),
        )
        conn.commit()
    finally:
        conn.close()
    return sam, alex, album_pk


def _login(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


class _StubQueue:
    def enqueue(self, **kwargs):
        return {'queued': True, 'reason': 'test'}


@pytest.fixture
def guarded(monkeypatch, profiles):
    sam, _alex, _album_id = profiles
    # the album belongs to sam; skip the real metadata planning
    monkeypatch.setattr(lr, 'resolve_album_profile_id', lambda *a, **k: sam)
    monkeypatch.setattr(lr, 'preview_album_reorganize', lambda **k: {'status': 'ok'})
    monkeypatch.setattr(rq, 'get_queue', lambda: _StubQueue())


def test_preview_refuses_another_profiles_album(client, guarded, profiles):
    _sam, alex, album_id = profiles
    _login(client, alex)
    r = client.post(f'/api/library/album/{album_id}/reorganize/preview', json={})
    assert r.status_code == 403, r.data


def test_preview_allows_the_owning_profile(client, guarded, profiles):
    sam, _alex, album_id = profiles
    _login(client, sam)
    r = client.post(f'/api/library/album/{album_id}/reorganize/preview', json={})
    assert r.status_code == 200, r.data


def test_preview_allows_admin_for_any_album(client, guarded, profiles):
    _sam, _alex, album_id = profiles
    _login(client, 1)
    r = client.post(f'/api/library/album/{album_id}/reorganize/preview', json={})
    assert r.status_code == 200, r.data


def test_apply_refuses_another_profiles_album(client, guarded, profiles):
    _sam, alex, album_id = profiles
    _login(client, alex)
    r = client.post(f'/api/library/album/{album_id}/reorganize', json={})
    assert r.status_code == 403, r.data


def test_apply_allows_the_owning_profile(client, guarded, profiles):
    sam, _alex, album_id = profiles
    _login(client, sam)
    r = client.post(f'/api/library/album/{album_id}/reorganize', json={})
    assert r.status_code == 200 and r.get_json()['success'], r.data


def test_apply_allows_admin_for_any_album(client, guarded, profiles):
    _sam, _alex, album_id = profiles
    _login(client, 1)
    r = client.post(f'/api/library/album/{album_id}/reorganize', json={})
    assert r.status_code == 200 and r.get_json()['success'], r.data
