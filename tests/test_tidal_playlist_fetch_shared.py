"""a big tidal playlist takes minutes to page through. the sync page's crawl,
the modal and start identifying each fetched it from scratch, so start sat
behind a second multi-minute fetch while the modal said 0 tracks. through the
real routes: one fetch at a time, start takes the one that just landed."""

from __future__ import annotations

import os
import tempfile
import threading
import time

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-tidalfetch-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

import api.source_playlists as sp  # noqa: E402
from core.tidal_client import Playlist, Track  # noqa: E402


class _SlowTidal:
    """get_playlist blocks until released, and counts how often it ran."""

    def __init__(self, authenticated=True):
        self.calls = 0
        self.release = threading.Event()
        self.started = threading.Event()
        self.authenticated = authenticated

    def is_authenticated(self):
        return self.authenticated

    def get_playlist(self, playlist_id):
        self.calls += 1
        self.started.set()
        self.release.wait(5)
        return Playlist(
            id=playlist_id, name='Driving Music',
            tracks=[Track(id=str(i), name=f't{i}', artists=['a'], album='al', duration_ms=1000)
                    for i in range(3)],
        )


class _NoExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append(args)
        return None


@pytest.fixture
def tidal(monkeypatch):
    client = _SlowTidal()
    monkeypatch.setattr(sp, 'get_tidal_client_for_profile', lambda: client)
    monkeypatch.setattr(sp, '_tidal_client', lambda: _SlowTidal(authenticated=False))
    monkeypatch.setattr(sp, 'tidal_discovery_executor', _NoExecutor())
    monkeypatch.setattr(sp, '_tidal_fetch_recent', {})
    monkeypatch.setattr(sp, '_tidal_fetch_inflight', {})
    monkeypatch.setattr(sp, 'tidal_discovery_states', {})
    return client


def _in_thread(fn):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault('r', fn()))
    t.start()
    return t, out


def test_the_modal_and_the_crawl_share_one_fetch(tidal):
    app = web_server.app
    crawl, crawl_out = _in_thread(lambda: app.test_client().get('/api/tidal/playlist/P1'))
    assert tidal.started.wait(5)
    modal, modal_out = _in_thread(lambda: app.test_client().get('/api/tidal/playlist/P1'))
    time.sleep(0.2)  # the modal's request is now waiting on the crawl's
    tidal.release.set()
    crawl.join(5)
    modal.join(5)
    assert tidal.calls == 1
    for out in (crawl_out, modal_out):
        assert out['r'].status_code == 200
        assert len(out['r'].get_json()['tracks']) == 3


def test_start_takes_the_fetch_that_just_landed_then_fetches_fresh(tidal):
    tidal.release.set()
    c = web_server.app.test_client()
    assert c.get('/api/tidal/playlist/P1').status_code == 200
    assert tidal.calls == 1

    r = c.post('/api/tidal/discovery/start/P1')
    assert r.status_code == 200, r.get_json()
    assert tidal.calls == 1  # no second fetch
    assert sp.tidal_discovery_states['P1']['spotify_total'] == 3

    # a re-run gets fresh tracks (#1613), not the handed-off copy again
    sp.tidal_discovery_states['P1']['phase'] = 'discovered'
    assert c.post('/api/tidal/discovery/start/P1').status_code == 200
    assert tidal.calls == 2


def test_start_does_not_take_a_stale_fetch(tidal, monkeypatch):
    tidal.release.set()
    c = web_server.app.test_client()
    c.get('/api/tidal/playlist/P1')
    client, fetched_at, playlist = sp._tidal_fetch_recent['P1']
    sp._tidal_fetch_recent['P1'] = (client, fetched_at - sp._TIDAL_FETCH_HANDOFF_SECONDS - 1, playlist)
    assert c.post('/api/tidal/discovery/start/P1').status_code == 200
    assert tidal.calls == 2


def test_start_uses_the_profiles_own_tidal_client(tidal):
    # the global client is signed out here; the profile's is not. start used
    # the global one while the page listed with the profile's.
    tidal.release.set()
    r = web_server.app.test_client().post('/api/tidal/discovery/start/P1')
    assert r.status_code == 200, r.get_json()
    assert tidal.calls == 1
