"""GET /api/library/radio — mode dispatch.

Seeded mode (track_id) is the classic similar-tracks refill; seedless mode
(?library=1) is the Library Radio starter (ranked-random across the whole
library). Neither param → 400.

The DB handle is STUBBED: under the full suite web_server is already imported
with some other module's DATABASE_PATH, so a real-DB version of this test read
a database full of other tests' tracks and the seedless random pool missed the
seeded rows (order-dependent flake). The selection logic has its own hermetic
in-memory coverage in tests/radio/ — what this file owns is the endpoint's
dispatch and parameter parsing, which the stub pins exactly.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-radio-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'r.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


class _FakeDb:
    def __init__(self):
        self.calls = []

    def get_radio_tracks(self, track_id, limit=20, exclude_ids=None):
        self.calls.append(('seeded', track_id, limit, exclude_ids))
        return {'success': True, 'tracks': [{'id': 'seeded-hit'}]}

    def get_library_radio_tracks(self, limit=50, exclude_ids=None):
        self.calls.append(('library', limit, exclude_ids))
        return {'success': True, 'tracks': [{'id': 'library-hit'}]}


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    return db


def test_no_seed_and_no_library_flag_is_400(client, fake_db):
    r = client.get('/api/library/radio')
    assert r.status_code == 400
    assert r.get_json()['success'] is False
    assert fake_db.calls == []          # rejected before any DB work


def test_library_mode_dispatches_seedless(client, fake_db):
    r = client.get('/api/library/radio?library=1&limit=10&exclude=a,b')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert [t['id'] for t in body['tracks']] == ['library-hit']
    assert fake_db.calls == [('library', 10, ['a', 'b'])]


def test_seeded_mode_dispatches_on_track_id(client, fake_db):
    r = client.get('/api/library/radio?track_id=t1&limit=25')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert [t['id'] for t in body['tracks']] == ['seeded-hit']
    assert fake_db.calls == [('seeded', 't1', 25, None)]


def test_track_id_wins_when_both_are_given(client, fake_db):
    # A seeded refill that happens to carry library=1 must stay seeded —
    # similar-tracks is the more specific ask.
    client.get('/api/library/radio?track_id=t9&library=1')
    assert fake_db.calls[0][0] == 'seeded'
