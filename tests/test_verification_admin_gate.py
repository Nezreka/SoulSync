"""#845 follow-up: the mutating verification-review endpoints (delete removes a
file from disk; approve flips verification state) must be admin-only, matching the
Phase 3 destructive-endpoint gating. The read/playback ones stay open."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-vgate-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'v.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _as_nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(4).hex()}')
    with client.session_transaction() as s:
        s['profile_id'] = pid
    return pid


def test_delete_is_admin_only(client):
    _as_nonadmin(client)
    assert client.post('/api/verification/1/delete').status_code == 403


def test_approve_is_admin_only(client):
    _as_nonadmin(client)
    assert client.post('/api/verification/1/approve').status_code == 403


def test_admin_not_blocked(client):
    # default session resolves to admin (profile 1) — must pass the gate (a
    # missing history id yields 404, NOT 403).
    assert client.post('/api/verification/999999/delete').status_code != 403
    assert client.post('/api/verification/999999/approve').status_code != 403
