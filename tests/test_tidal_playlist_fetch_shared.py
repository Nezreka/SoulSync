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


class _InlineExecutor:
    """runs start's fetch-and-mirror step right away. the discovery itself
    is stubbed out in the fixture."""

    def submit(self, fn, *args):
        fn(*args)
        return None


@pytest.fixture
def tidal(monkeypatch):
    client = _SlowTidal()
    monkeypatch.setattr(sp, 'get_tidal_client_for_profile', lambda: client)
    monkeypatch.setattr(sp, '_tidal_client', lambda: _SlowTidal(authenticated=False))
    monkeypatch.setattr(sp, 'tidal_discovery_executor', _InlineExecutor())
    monkeypatch.setattr(sp, '_run_tidal_discovery_worker', lambda *a: None)
    monkeypatch.setattr(sp, 'add_activity_item', lambda *a, **k: None)
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


def test_start_mirrors_a_playlist_that_has_no_mirror(tidal):
    """TheHomeGuy: Driving Music (1218 tracks) synced to the server and showed
    on the dashboard, but never landed in the mirrored list. the page only
    mirrored from its background crawl, which a big playlist could lose."""
    from database.music_database import get_database

    tidal.release.set()
    db = get_database()
    assert db.get_mirrored_playlist_by_source('tidal', 'NEW1', 1) is None

    r = web_server.app.test_client().post('/api/tidal/discovery/start/NEW1')
    assert r.status_code == 200, r.get_json()

    mirror = db.get_mirrored_playlist_by_source('tidal', 'NEW1', 1)
    assert mirror is not None
    assert mirror['name'] == 'Driving Music'
    tracks = db.get_mirrored_playlist_tracks(mirror['id'])
    assert [t['source_track_id'] for t in tracks] == ['0', '1', '2']
    assert tracks[0]['track_name'] == 't0' and tracks[0]['artist_name'] == 'a'


def test_start_leaves_an_existing_mirrors_tracks_alone(tidal):
    from database.music_database import get_database

    tidal.release.set()
    db = get_database()
    pid = db.mirror_playlist('tidal', 'OLD1', 'Driving Music',
                             [{'track_name': 'mine', 'artist_name': 'x', 'source_track_id': 'z'}],
                             profile_id=1)
    assert web_server.app.test_client().post('/api/tidal/discovery/start/OLD1').status_code == 200
    assert [t['source_track_id'] for t in db.get_mirrored_playlist_tracks(pid)] == ['z']


def test_start_answers_before_the_fetch_lands(tidal, monkeypatch):
    """TheHomeGuy: start waited on the whole 1218-track fetch inside the
    request, so the reverse proxy 504'd and the modal said it couldn't start,
    then discovery kicked off on its own minutes later."""
    class _Later:
        def __init__(self):
            self.jobs = []

        def submit(self, fn, *args):
            self.jobs.append((fn, args))

    later = _Later()
    monkeypatch.setattr(sp, 'tidal_discovery_executor', later)
    c = web_server.app.test_client()

    r = c.post('/api/tidal/discovery/start/P1')
    assert r.status_code == 200, r.get_json()
    assert tidal.calls == 0  # nothing fetched inside the request
    assert c.get('/api/tidal/discovery/status/P1').get_json()['phase'] == 'discovering'

    tidal.release.set()
    fn, args = later.jobs[0]
    fn(*args)
    assert tidal.calls == 1
    assert sp.tidal_discovery_states['P1']['spotify_total'] == 3


def test_a_failed_fetch_parks_the_state_on_error(tidal, monkeypatch):
    monkeypatch.setattr(tidal, 'get_playlist', lambda pid: None)
    c = web_server.app.test_client()
    assert c.post('/api/tidal/discovery/start/P1').status_code == 200
    body = c.get('/api/tidal/discovery/status/P1').get_json()
    assert body['phase'] == 'error'
    # and a second go is allowed, not 'already in progress'
    assert c.post('/api/tidal/discovery/start/P1').status_code == 200
