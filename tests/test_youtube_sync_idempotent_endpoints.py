"""#702: cancel/reset/delete of a mirrored-playlist sync whose in-memory state is
gone (restart/eviction) must return success, not 404 'YouTube playlist not found'
— otherwise the playlist is permanently wedged."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ytsync-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'y.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


_GONE = 'state_wiped_by_restart_hash'


def test_cancel_missing_state_is_success(client):
    r = client.post(f'/api/youtube/sync/cancel/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True


def test_reset_missing_state_is_success(client):
    r = client.post(f'/api/youtube/reset/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True


def test_delete_missing_state_is_success(client):
    r = client.delete(f'/api/youtube/delete/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True
