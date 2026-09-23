"""basic search downloads become real batches with pinned tasks.

the user already picked the exact file, so each task downloads exactly that
file (no search, no swap) and the batch shows on the Downloads page like any
other. these pin the pieces: the batch/task shape, dispatch, failure
reporting, the size filter skip, the simple-download context, and the
wishlist opt-out.
"""

from __future__ import annotations

import pytest

from core.downloads import candidates as dc
from core.downloads import pinned_batch as pb
from core.runtime_state import (
    download_batches,
    download_tasks,
    matched_downloads_context,
)
from tests.downloads.test_downloads_candidates import _build_deps, _Candidate, _Track


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    download_batches.clear()
    matched_downloads_context.clear()


def _result(**over):
    base = {
        'username': 'peer', 'filename': r'Music\Aphex Twin\SAW\01 - Xtal.flac', 'size': 1234,
        'bitrate': 1411, 'duration': 290_000, 'quality': 'flac', 'free_upload_slots': 1,
        'upload_speed': 5, 'queue_length': 0, 'artist': 'Aphex Twin', 'title': 'Xtal',
        'album': 'SAW', 'track_number': 1,
    }
    base.update(over)
    return base


class _Deps:
    def __init__(self, attempt=lambda *a, **k: True):
        self.calls = []
        self.monitored = []
        self.completed = []
        self._attempt = attempt

    def deps(self):
        def submit(fn, *args):
            self.calls.append('submit')
            fn(*args)

        def attempt(task_id, candidates, track, batch_id):
            self.calls.append(('attempt', task_id, candidates, track, batch_id))
            return self._attempt(task_id, candidates, track, batch_id)

        return pb.PinnedBatchDeps(
            start_monitoring=lambda bid: (self.monitored.append(bid), self.calls.append('monitor')),
            submit=submit,
            attempt_download_with_candidates=attempt,
            on_download_completed=lambda bid, tid, success=True: self.completed.append((bid, tid, success)),
        )


def test_release_level_sources_are_not_pinnable():
    assert pb.is_pinnable('peer')
    assert pb.is_pinnable('deezer_dl')
    for source in ('torrent', 'usenet', 'lidarr', 'Torrent'):
        assert not pb.is_pinnable(source)
    assert not pb.is_pinnable('')
    assert not pb.is_pinnable(None)


def test_batch_and_tasks_are_shaped_for_the_downloads_page():
    files = [
        pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result())),
        pb.PinnedFile(candidate=pb.candidate_from_result(_result(filename='b.flac', title='Tha')),
                      track_info=pb.simple_track_info(_result(title='Tha'))),
    ]
    batch_id, task_ids = pb.create_pinned_batch(files, name='SAW', profile_id=3)

    batch = download_batches[batch_id]
    assert batch['queue'] == task_ids
    assert batch['playlist_name'] == 'SAW'
    assert batch['source_page'] == 'Search'
    assert batch['total_tracks'] == 2
    assert batch['profile_id'] == 3
    assert batch['skip_failed_wishlist'] is True
    # every slot is taken up front, nothing is left for the queue walker
    assert batch['queue_index'] == 2 and batch['active_count'] == 2

    first = download_tasks[task_ids[0]]
    assert first['batch_id'] == batch_id
    assert first['_user_manual_pick'] is True
    assert first['_pinned_candidate']['filename'].endswith('01 - Xtal.flac')
    assert first['cached_candidates'] == [first['_pinned_candidate']]
    assert first['track_info']['_simple_download'] is True
    assert first['track_info']['_simple_search_result']['is_simple_download'] is True


def test_dispatch_monitors_first_then_downloads_exactly_the_pinned_file():
    harness = _Deps()
    batch_id, task_ids = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result()))],
        name='x', profile_id=1)

    pb.dispatch_pinned_batch(batch_id, task_ids, harness.deps())

    assert harness.calls[0] == 'monitor'
    _, task_id, candidates, track, bid = harness.calls[2]
    assert (task_id, bid) == (task_ids[0], batch_id)
    assert len(candidates) == 1
    assert candidates[0].username == 'peer'
    assert candidates[0].filename.endswith('01 - Xtal.flac')
    assert candidates[0].confidence == 1.0
    assert track.name == 'Xtal' and track.artists == ['Aphex Twin'] and track.album == 'SAW'
    assert harness.completed == []


def test_a_file_that_never_starts_fails_the_task_and_tells_the_batch():
    harness = _Deps(attempt=lambda *a: False)
    batch_id, task_ids = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result()))],
        name='x', profile_id=1)

    pb.dispatch_pinned_batch(batch_id, task_ids, harness.deps())

    assert download_tasks[task_ids[0]]['status'] == 'failed'
    assert harness.completed == [(batch_id, task_ids[0], False)]


def test_an_exception_starting_it_is_a_failure_not_a_stuck_batch():
    def boom(*_a):
        raise RuntimeError('peer went away')

    harness = _Deps(attempt=boom)
    batch_id, task_ids = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result()))],
        name='x', profile_id=1)

    pb.dispatch_pinned_batch(batch_id, task_ids, harness.deps())

    assert download_tasks[task_ids[0]]['status'] == 'failed'
    assert 'peer went away' in download_tasks[task_ids[0]]['error_message']
    assert harness.completed == [(batch_id, task_ids[0], False)]


def test_already_terminal_is_not_reported_twice():
    # the candidate helper frees the slot itself on a cancel after start
    def cancel_then_fail(task_id, *_a):
        download_tasks[task_id]['status'] = 'cancelled'
        return False

    harness = _Deps(attempt=cancel_then_fail)
    batch_id, task_ids = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result()))],
        name='x', profile_id=1)

    pb.dispatch_pinned_batch(batch_id, task_ids, harness.deps())

    assert harness.completed == []


def test_a_task_cancelled_before_it_runs_is_left_alone():
    harness = _Deps()
    batch_id, task_ids = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=pb.simple_track_info(_result()))],
        name='x', profile_id=1)
    download_tasks[task_ids[0]]['status'] = 'cancelled'

    pb.dispatch_pinned_batch(batch_id, task_ids, harness.deps())

    assert not any(isinstance(c, tuple) for c in harness.calls)


# ── the candidate handoff ──────────────────────────────────────────────────


def test_a_pinned_file_survives_the_size_filter(monkeypatch):
    # the filter drops bad SEARCH results. the one file the user chose must
    # not be silently dropped by it
    import core.downloads.size_limit as size_limit
    monkeypatch.setattr(size_limit, 'filter_music_candidates', lambda c, **k: [])
    deps = _build_deps()

    download_tasks['pinned'] = {'status': 'queued', 'track_info': {}, 'used_sources': set(),
                                'download_id': None, '_pinned_candidate': {'filename': 'song.flac'}}
    assert dc.attempt_download_with_candidates('pinned', [_Candidate()], _Track(), batch_id='b', deps=deps)

    download_tasks['searched'] = {'status': 'queued', 'track_info': {}, 'used_sources': set(),
                                  'download_id': None}
    assert not dc.attempt_download_with_candidates('searched', [_Candidate(filename='other.flac')],
                                                   _Track(), batch_id='b', deps=deps)


def test_an_as_is_download_gets_the_context_the_simple_branch_reads():
    deps = _build_deps()
    info = pb.simple_track_info(_result(filename='song.flac'))
    download_tasks['t'] = {'status': 'queued', 'track_info': info, 'used_sources': set(),
                           'download_id': None, '_user_manual_pick': True}

    assert dc.attempt_download_with_candidates('t', [_Candidate()], _Track(), batch_id='b', deps=deps)

    ctx = matched_downloads_context['user1::song.flac']
    assert ctx['search_result']['is_simple_download'] is True
    assert ctx['search_result']['title'] == 'Xtal'
    assert ctx['task_id'] == 't' and ctx['batch_id'] == 'b'


def test_an_enriched_download_does_not_get_a_simple_context():
    deps = _build_deps()
    download_tasks['t'] = {'status': 'queued', 'track_info': {'name': 'Xtal'}, 'used_sources': set(),
                           'download_id': None}
    assert dc.attempt_download_with_candidates('t', [_Candidate()], _Track(), batch_id='b', deps=deps)
    assert 'search_result' not in matched_downloads_context['user1::song.flac']


# ── wishlist opt-out ───────────────────────────────────────────────────────


def test_failed_basic_search_files_are_not_wishlisted(monkeypatch):
    from core.downloads import wishlist_failed as wf

    added = []

    class _Service:
        def add_failed_track_from_modal(self, **kw):
            added.append(kw)
            return True

    import core.wishlist_service as ws
    monkeypatch.setattr(ws, 'get_wishlist_service', lambda: _Service())
    monkeypatch.setattr(wf, '_remove_completed_tracks_from_wishlist', lambda *a, **k: None)

    download_batches['b'] = {
        'skip_failed_wishlist': True,
        'permanently_failed_tracks': [{'track_name': 'Xtal', 'track_data': {'id': 'x', 'name': 'Xtal'}}],
        'cancelled_tracks': set(),
        'profile_id': 1,
    }
    result = wf._process_failed_tracks_to_wishlist_exact('b')
    assert added == []
    assert result['tracks_added'] == 0


def test_the_profile_rides_on_track_info_so_it_outlives_the_batch():
    # post-processing reads it from track_info when the batch is already gone,
    # which is what files an own-library profile's download in its own folder
    from core.imports.paths import import_profile_id

    info = pb.simple_track_info(_result())
    _batch_id, (task_id,) = pb.create_pinned_batch(
        [pb.PinnedFile(candidate=pb.candidate_from_result(_result()), track_info=info)],
        name='x', profile_id=7)
    download_batches.clear()
    assert import_profile_id({'track_info': download_tasks[task_id]['track_info']}) == 7
    assert 'profile_id' not in info   # the caller's dict isn't touched
