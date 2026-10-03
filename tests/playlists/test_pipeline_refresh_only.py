"""Refresh from source (#1413).

    "If I then go to Youtube and add another song to the playlist what do I
    need to do to reflect this change in SoulSync? ... Can we have a button
    like Update/Pull from source"

Sync & download already refreshes first, but it also pushes to the server
and downloads what's missing. refresh_only runs the same pipeline and stops
after refresh + discovery: the new track list, the new tracks discovered,
existing matches kept, nothing pushed, nothing downloaded.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.playlists import pipeline
from database.music_database import MusicDatabase
from tests.automation.test_playlist_pipeline_folder_mode import _minimal_deps


@pytest.fixture
def db(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.mirror_playlist(source='youtube', source_playlist_id='PLx', name='Road Trip',
                       tracks=[{'track_name': 'A', 'artist_name': 'X', 'source_track_id': 'a'}],
                       profile_id=1)
    return db


def _run(db, **config):
    calls = {'refresh': 0, 'discover': 0, 'sync': 0}
    progress = []

    def refresh_fn(cfg, deps):
        calls['refresh'] += 1
        return {'refreshed': '1', 'errors': '0'}

    def discover(*a, **k):
        calls['discover'] += 1

    def sync_and_wishlist(*a, **k):
        calls['sync'] += 1
        return {'synced': 1, 'skipped': 0, 'wishlist_queued': 1}

    deps = _minimal_deps(
        get_database=lambda: db,
        run_playlist_discovery_worker=discover,
        update_progress=lambda _aid, **k: progress.append(k),
    )
    pl_id = db.get_mirrored_playlists(1)[0]['id']
    result = pipeline.run_mirrored_playlist_pipeline(
        {'playlist_id': str(pl_id), 'profile_id': 1, '_automation_id': 'mirrored_x', **config},
        deps,
        refresh_fn=refresh_fn,
        sync_one_fn=lambda *a, **k: {},
        sync_and_wishlist_fn=sync_and_wishlist,
    )
    return result, calls, progress


def test_refresh_only_pulls_and_discovers_but_never_pushes_or_downloads(db):
    result, calls, progress = _run(db, refresh_only=True)
    assert result['status'] == 'completed'
    assert calls == {'refresh': 1, 'discover': 1, 'sync': 0}
    assert result['tracks_synced'] == '0'
    assert result['wishlist_queued'] == '0'
    assert progress[-1]['phase'] == 'Refreshed from source'


def test_the_full_pipeline_still_pushes_and_downloads(db):
    result, calls, progress = _run(db)
    assert calls == {'refresh': 1, 'discover': 1, 'sync': 1}
    assert progress[-1]['phase'] == 'Pipeline complete'


# ── the endpoint passes it through ─────────────────────────────────────────

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-refresh-only-')


@pytest.fixture
def client(monkeypatch):
    os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'refresh.db'))
    os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')
    web_server = pytest.importorskip('web_server')
    import api.mirrored_playlists as mp

    started = []

    class _Thread:
        def __init__(self, target, args, **kw):
            self.target, self.args = target, args

        def start(self):
            started.append(self.args)

    class _State:
        def is_pipeline_running(self):
            return False

    class _Deps:
        state = _State()

    monkeypatch.setattr(mp.threading, 'Thread', _Thread)
    monkeypatch.setattr(mp, '_get_automation_deps', lambda: _Deps())
    monkeypatch.setattr(mp, '_owned_mirrored_playlist',
                        lambda database, pid: {'id': pid, 'name': 'Road Trip', 'source': 'youtube'})
    return web_server.app.test_client(), started


def test_endpoint_refresh_only(client):
    c, started = client
    r = c.post('/api/mirrored-playlists/7/pipeline/run', json={'refresh_only': True})
    assert r.status_code == 200, r.data
    assert r.get_json()['state']['phase'] == 'Refreshing from source...'
    assert started[-1][-1] is True  # refresh_only reaches the runner


def test_endpoint_default_is_the_full_pipeline(client):
    c, started = client
    r = c.post('/api/mirrored-playlists/7/pipeline/run', json={})
    assert r.get_json()['state']['phase'] == 'Starting pipeline...'
    assert started[-1][-1] is False
