"""stopping a wishlist run stops all of it.

a wishlist run is split into one batch per album plus a per-track batch,
all sharing wishlist_run_id. the page polls the first batch id, so cancel
on that id used to leave every other album downloading. and a stop that
landed while the manual run was still preparing got undone when the
placeholder was rebuilt as the first album batch.
"""

from __future__ import annotations

import os
import tempfile
import threading
from types import SimpleNamespace

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-runstop-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'runstop.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.wishlist.processing import _run_wishlist_cycle  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    from core.runtime_state import download_batches, download_tasks
    monkeypatch.setattr(web_server, '_record_sync_history_completion', lambda bid, b: None)
    rows = {
        'rs-first': ('run-A', 'downloading', ['rs-t1']),
        'rs-album2': ('run-A', 'downloading', ['rs-t2']),
        'rs-residual': ('run-A', 'queued', ['rs-t3']),
        'rs-other-run': ('run-B', 'downloading', ['rs-t4']),
        'rs-done': ('run-A', 'complete', ['rs-t5']),
    }
    for bid, (run_id, phase, queue) in rows.items():
        download_batches[bid] = {
            'phase': phase, 'playlist_id': 'wishlist', 'playlist_name': 'Wishlist',
            'queue': queue, 'wishlist_run_id': run_id, 'permanently_failed_tracks': [],
            'cancelled_tracks': set(), 'analysis_results': [],
        }
        for tid in queue:
            download_tasks[tid] = {'status': 'downloading' if phase != 'complete' else 'completed'}
    yield download_batches, download_tasks
    for bid in rows:
        download_batches.pop(bid, None)
    for n in range(1, 6):
        download_tasks.pop(f'rs-t{n}', None)


def test_stop_cancels_every_batch_in_the_run(run):
    batches, tasks = run
    resp = web_server.app.test_client().post('/api/playlists/rs-first/cancel_batch')
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body['cancelled_batches'] == 3
    assert body['cancelled_tasks'] == 3
    for bid in ('rs-first', 'rs-album2', 'rs-residual'):
        assert batches[bid]['phase'] == 'cancelled'
    # another run and a finished sibling are left alone
    assert batches['rs-other-run']['phase'] == 'downloading'
    assert tasks['rs-t4']['status'] == 'downloading'
    assert batches['rs-done']['phase'] == 'complete'


def test_a_plain_batch_still_cancels_alone(run):
    batches, _tasks = run
    batches['rs-plain'] = {'phase': 'downloading', 'playlist_id': 'pl', 'queue': [],
                           'permanently_failed_tracks': [], 'cancelled_tracks': set(),
                           'analysis_results': []}
    try:
        body = web_server.app.test_client().post('/api/playlists/rs-plain/cancel_batch').get_json()
        assert body['cancelled_batches'] == 1
        assert batches['rs-first']['phase'] == 'downloading'
    finally:
        batches.pop('rs-plain', None)


def _runtime(batches):
    submitted = []

    class _Exec:
        def submit(self, fn, *a, **k):
            submitted.append(fn)

    quiet = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                            error=lambda *a, **k: None, debug=lambda *a, **k: None)
    return SimpleNamespace(
        download_batches=batches, tasks_lock=threading.Lock(), missing_download_executor=_Exec(),
        album_bundle_executor=None, run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3, current_time_fn=__import__('time').time,
        profile_id=1, logger=quiet, _submitted=submitted,
    )


def _track(n):
    return {'track_id': f'sp-{n}', 'spotify_track_id': f'sp-{n}', 'name': f'Song {n}',
            'artists': [{'name': 'A'}], 'profile_id': 1}


def test_a_stop_during_prep_is_not_undone():
    batches = {'placeholder': {'phase': 'cancelled', 'playlist_id': 'wishlist'}}
    runtime = _runtime(batches)
    result = _run_wishlist_cycle(runtime, playlist_id='wishlist', cycle='albums',
                                 tracks=[_track(1), _track(2)], run_id='r', auto_initiated=False,
                                 first_batch_id='placeholder')
    assert result == {'submitted': [], 'album_batches': 0, 'residual_count': 0}
    assert batches == {'placeholder': {'phase': 'cancelled', 'playlist_id': 'wishlist'}}
    assert runtime._submitted == []


def test_an_unstopped_placeholder_still_dispatches():
    batches = {'placeholder': {'phase': 'analysis', 'playlist_id': 'wishlist'}}
    runtime = _runtime(batches)
    result = _run_wishlist_cycle(runtime, playlist_id='wishlist', cycle='albums',
                                 tracks=[_track(1)], run_id='r', auto_initiated=False,
                                 first_batch_id='placeholder')
    assert result['submitted']
    assert batches['placeholder']['phase'] != 'cancelled'
