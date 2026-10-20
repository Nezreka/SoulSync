"""a finished batch stays alive while its modal is open, or while a manual
candidate pick on one of its rows is still downloading.

spectro (discord): picking candidates for the not-found rows of a finished
download died a few minutes in. the candidates call 404'd "Task not found"
because the healer dropped every finished batch 5 minutes after completion,
modal open or not. the 5 minutes now count from the last time anyone looked.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def env(monkeypatch):
    import web_server
    from core.runtime_state import download_batches, download_tasks

    monkeypatch.setattr(web_server, '_check_batch_completion_v2', lambda _b: None)
    monkeypatch.setattr(web_server, '_start_next_batch_of_downloads', lambda _b: None)
    monkeypatch.setattr(web_server, '_list_available_download_sources', lambda: ('soulseek', []))
    download_batches.clear()
    download_tasks.clear()

    def _finished(batch_id='b1', task_id='t1', done_ago=0, **extra):
        download_tasks[task_id] = {'status': 'not_found', 'track_index': 0,
                                   'batch_id': batch_id, 'track_info': {'name': 'x'},
                                   'cached_candidates': []}
        download_batches[batch_id] = {'queue': [task_id], 'queue_index': 1,
                                      'active_count': 0, 'max_concurrent': 3,
                                      'phase': 'complete',
                                      'completion_time': time.time() - done_ago, **extra}

    web_server.app.config['TESTING'] = True
    try:
        yield {'ws': web_server, 'batches': download_batches, 'tasks': download_tasks,
               'finished': _finished, 'client': web_server.app.test_client()}
    finally:
        download_batches.clear()
        download_tasks.clear()


def test_an_unwatched_finished_batch_is_still_cleaned_up(env):
    env['finished'](done_ago=400)
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' not in env['batches']
    assert 't1' not in env['tasks']


def test_an_open_modal_keeps_the_batch_past_five_minutes(env):
    """spectro's case through the real route: the poll names the open modal."""
    env['finished'](done_ago=400)
    r = env['client'].get('/api/download_status/batch?batch_ids=b1&watching=b1')
    assert r.status_code == 200
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' in env['batches']
    r = env['client'].get('/api/downloads/task/t1/candidates')
    assert r.status_code == 200, r.get_data(as_text=True)


def test_polling_without_watching_does_not_keep_it(env):
    """a hidden modal still polls. that alone must not pin the batch forever."""
    env['finished'](done_ago=400)
    env['client'].get('/api/download_status/batch?batch_ids=b1')
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' not in env['batches']


def test_opening_candidates_counts_as_watching(env):
    env['finished'](done_ago=400)
    assert env['client'].get('/api/downloads/task/t1/candidates').status_code == 200
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' in env['batches']


def test_five_minutes_after_the_modal_closes_it_goes(env):
    env['finished'](done_ago=2000)
    env['batches']['b1']['_watched_at'] = time.time() - 301
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' not in env['batches']


def test_watching_a_wishlist_run_keeps_every_sibling(env):
    """the modal polls one id of a split wishlist run; its siblings hold rows too."""
    env['finished']('b1', 't1', done_ago=400, wishlist_run_id='run')
    env['finished']('b2', 't2', done_ago=400, wishlist_run_id='run')
    env['finished']('b3', 't3', done_ago=400, wishlist_run_id='other')
    env['client'].get('/api/download_status/batch?batch_ids=b1&watching=b1')
    env['ws'].validate_and_heal_batch_states()
    assert set(env['batches']) == {'b1', 'b2'}


def test_watching_an_unknown_batch_is_harmless(env):
    r = env['client'].get('/api/download_status/batch?batch_ids=nope&watching=nope')
    assert r.status_code == 200


def test_a_backgrounded_tab_keepalive_counts_as_watching(env):
    """a hidden tab skips the status poll; it pings /watch instead."""
    env['finished'](done_ago=400)
    r = env['client'].post('/api/download_status/watch', json={'batch_ids': ['b1']})
    assert r.status_code == 200
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' in env['batches']


def test_the_keepalive_rejects_junk(env):
    assert env['client'].post('/api/download_status/watch', json={'batch_ids': 'b1'}).status_code == 400
    assert env['client'].post('/api/download_status/watch', data='nope').status_code == 400


def test_a_manual_pick_still_downloading_holds_the_batch(env):
    """nobody watching, completed long ago, but the user's pick is mid-download."""
    env['finished'](done_ago=2000)
    env['tasks']['t1']['status'] = 'downloading'
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' in env['batches']
    env['tasks']['t1']['status'] = 'completed'
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' not in env['batches']


def test_a_task_already_counted_done_does_not_hold_it(env):
    """a zombie status the healer already ignores must not pin the batch forever."""
    env['finished'](done_ago=2000)
    env['tasks']['t1']['status'] = 'downloading'
    env['batches']['b1']['_completed_task_ids'] = {'t1'}
    env['ws'].validate_and_heal_batch_states()
    assert 'b1' not in env['batches']


def test_closing_the_modal_mid_pick_defers_the_cleanup(env):
    env['finished'](done_ago=10)
    env['tasks']['t1']['status'] = 'post_processing'
    r = env['client'].post('/api/playlists/cleanup_batch', json={'batch_id': 'b1'})
    assert r.status_code == 200 and r.get_json()['deferred'] is True
    assert 't1' in env['tasks']


def test_closing_a_settled_modal_still_cleans_up_at_once(env):
    env['finished'](done_ago=10)
    r = env['client'].post('/api/playlists/cleanup_batch', json={'batch_id': 'b1'})
    assert r.status_code == 200 and not r.get_json().get('deferred')
    assert 'b1' not in env['batches'] and 't1' not in env['tasks']
