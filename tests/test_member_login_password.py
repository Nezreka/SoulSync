"""Admin sets a member's LOGIN password (the gap behind 'non-admins can't log in
when Require Login is on'). The endpoint already allowed admin→anyone; this locks
that the round-trip actually lets the member authenticate."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-memberpw-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'm.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _make_member(db, pid=77):
    conn = db._get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO profiles (id, name, is_admin) VALUES (?,?,0)", (pid, 'Member'))
        conn.commit()
    finally:
        conn.close()


def test_admin_sets_member_password_then_member_can_authenticate(client):
    db = web_server.get_database()
    _make_member(db)
    assert db.verify_profile_password(77, 'secret123') is False     # no password → can't log in

    r = client.post('/api/profiles/77/set-password', json={'password': 'secret123'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True and body['has_password'] is True

    assert db.verify_profile_password(77, 'secret123') is True       # member can now authenticate
    assert db.verify_profile_password(77, 'wrong') is False


def test_admin_can_clear_member_password(client):
    db = web_server.get_database()
    _make_member(db, pid=78)
    client.post('/api/profiles/78/set-password', json={'password': 'pw12345'})
    assert db.verify_profile_password(78, 'pw12345') is True
    r = client.post('/api/profiles/78/set-password', json={'password': ''})
    assert r.status_code == 200
    assert db.verify_profile_password(78, 'pw12345') is False         # cleared → no login again
