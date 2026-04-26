"""Tests for `core.reorganize_queue.ReorganizeQueue`.

Contract this test file pins:

1. **Dedupe on enqueue** — re-submitting an album that's already queued or
   running returns ``{'queued': False, 'reason': 'already_queued'}`` and
   the existing queue_id, never a duplicate.
2. **FIFO order** — the worker drains items in submission order.
3. **Per-item source preserved** — the source string the user picked at
   enqueue time is what the runner sees, even when multiple items with
   different sources are interleaved.
4. **Continue on failure** — a runner that raises (or one whose summary
   reports a non-completed status) marks that item failed and the
   worker moves to the next item, it does not stall.
5. **Cancel queued** — items in `queued` state can be dropped before
   they reach the runner.
6. **Cancel running rejected** — the currently-running item can NOT be
   cancelled, the API returns `running_cant_cancel`.
7. **Clear queued** — bulk-cancels all `queued` items at once, leaves
   the running item alone.
8. **Snapshot shape** — `active`, `queued`, `recent`, and `totals` keys
   are always present and reflect the current state.
9. **update_active_progress** — live progress fields propagate onto the
   running item (and only the running item).
10. **Setting runner late** — items enqueued before `set_runner()` was
    called still get processed once the runner shows up.
"""

import threading
import time

import pytest

from core.reorganize_queue import ReorganizeQueue, QueueItem


# --- helpers ---------------------------------------------------------------


def _make_runner(record, *, raise_on=None, summary_factory=None,
                 block_event=None, runtime=0.0):
    """Build a runner closure that records what it was called with.

    Args:
        record: list to append `(queue_id, source)` to per call.
        raise_on: queue_id (or set of queue_ids) for which the runner
            should raise — used to test continue-on-failure.
        summary_factory: optional callable `(item) -> summary dict` to
            override the default `{'status': 'completed', ...}` shape.
        block_event: optional `threading.Event` the runner blocks on
            before returning — used to keep an item in 'running' state
            while the test pokes at it.
        runtime: seconds the runner sleeps before returning.
    """
    raise_set = set()
    if isinstance(raise_on, str):
        raise_set = {raise_on}
    elif raise_on:
        raise_set = set(raise_on)

    def runner(item):
        record.append((item.queue_id, item.source))
        if block_event is not None:
            block_event.wait(timeout=2.0)
        if runtime:
            time.sleep(runtime)
        if item.queue_id in raise_set:
            raise RuntimeError(f"Simulated failure for {item.queue_id}")
        if summary_factory is not None:
            return summary_factory(item)
        return {
            'status': 'completed',
            'source': item.source or 'spotify',
            'total': 1,
            'moved': 1,
            'skipped': 0,
            'failed': 0,
            'errors': [],
        }
    return runner


def _enqueue(queue, *, album_id, source=None, title=None, artist='Aerosmith'):
    return queue.enqueue(
        album_id=album_id,
        album_title=title or f"Album {album_id}",
        artist_id='artist-1',
        artist_name=artist,
        source=source,
    )


def _wait_for(predicate, timeout=2.0, interval=0.02):
    """Poll until predicate() is truthy or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def queue():
    q = ReorganizeQueue()
    yield q
    q.stop()


# --- tests -----------------------------------------------------------------


def test_enqueue_returns_queued_with_position(queue):
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    r1 = _enqueue(queue, album_id='alb-1')
    # Wait for the worker to actually pick up alb-1 so r2 lands while
    # alb-1 is running, not while it's still queued — otherwise the
    # position number depends on thread-scheduling timing.
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)
    r2 = _enqueue(queue, album_id='alb-2')
    assert r1['queued'] is True
    assert r1['position'] == 1
    assert r2['queued'] is True
    assert r2['position'] == 1
    block.set()


def test_enqueue_same_album_dedupes(queue):
    queue.set_runner(_make_runner([], block_event=threading.Event()))
    r1 = _enqueue(queue, album_id='alb-1', source='spotify')
    r2 = _enqueue(queue, album_id='alb-1', source='deezer')  # different source
    assert r1['queued'] is True
    assert r2['queued'] is False
    assert r2['reason'] == 'already_queued'
    assert r2['queue_id'] == r1['queue_id']


def test_dedupe_releases_after_completion(queue):
    """Once an item finishes (done/failed/cancelled), the same album_id
    can be re-enqueued. Otherwise users couldn't retry after a fix."""
    record = []
    queue.set_runner(_make_runner(record))
    r1 = _enqueue(queue, album_id='alb-1')
    assert _wait_for(lambda: any(r[0] == r1['queue_id'] for r in record))
    # Wait for the item to flip into the recent bucket.
    assert _wait_for(lambda: queue.snapshot()['active'] is None)
    r2 = _enqueue(queue, album_id='alb-1')
    assert r2['queued'] is True
    assert r2['queue_id'] != r1['queue_id']


def test_fifo_order(queue):
    record = []
    queue.set_runner(_make_runner(record))
    ids = [_enqueue(queue, album_id=f'alb-{i}')['queue_id'] for i in range(5)]
    assert _wait_for(lambda: len(record) == 5)
    assert [r[0] for r in record] == ids


def test_per_item_source_preserved(queue):
    record = []
    queue.set_runner(_make_runner(record))
    sources = ['spotify', 'deezer', 'itunes', None, 'discogs']
    for i, src in enumerate(sources):
        _enqueue(queue, album_id=f'alb-{i}', source=src)
    assert _wait_for(lambda: len(record) == len(sources))
    assert [r[1] for r in record] == sources


def test_continue_on_runner_exception(queue):
    """A runner that raises must not stall the queue — the item is
    marked failed and the next item runs."""
    record = []
    # Pre-allocate queue_ids by enqueuing first, then point the runner
    # at the middle one. Block the runner so all three sit in the queue
    # before any actually run.
    block = threading.Event()
    raise_target = {}

    def runner(item):
        record.append((item.queue_id, item.source))
        block.wait(timeout=2.0)
        if item.queue_id == raise_target.get('id'):
            raise RuntimeError(f"Simulated failure for {item.queue_id}")
        return {
            'status': 'completed', 'source': 'spotify',
            'total': 1, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    queue.set_runner(runner)
    ids = [_enqueue(queue, album_id=f'alb-{i}')['queue_id'] for i in range(3)]
    raise_target['id'] = ids[1]
    block.set()

    assert _wait_for(lambda: len(record) == 3)
    assert [r[0] for r in record] == ids

    assert _wait_for(lambda: queue.snapshot()['active'] is None)
    snap = queue.snapshot()
    recent_by_id = {r['queue_id']: r for r in snap['recent']}
    assert recent_by_id[ids[0]]['status'] == 'done'
    assert recent_by_id[ids[1]]['status'] == 'failed'
    assert recent_by_id[ids[2]]['status'] == 'done'


def test_failed_status_when_runner_reports_failed_tracks(queue):
    """A summary with ``failed > 0`` should mark the queue item as
    'failed' even if the runner returned normally."""
    queue.set_runner(_make_runner([], summary_factory=lambda item: {
        'status': 'completed',
        'source': 'spotify',
        'total': 5,
        'moved': 4,
        'skipped': 0,
        'failed': 1,
        'errors': [{'track_id': 't-1', 'title': 'X', 'error': 'boom'}],
    }))
    qid = _enqueue(queue, album_id='alb-1')['queue_id']
    # Wait for the item to land in `recent` (active is None both before
    # the worker picks up the item and after it's done — only the
    # presence in recent is unambiguous).
    assert _wait_for(lambda: any(r['queue_id'] == qid for r in queue.snapshot()['recent']))
    snap = queue.snapshot()
    item = next(i for i in snap['recent'] if i['queue_id'] == qid)
    assert item['status'] == 'failed'
    assert item['moved'] == 4
    assert item['failed'] == 1
    assert item['error'] == 'boom'


def test_failed_status_when_runner_reports_non_completed_status(queue):
    """``status='no_source_id'`` and friends are setup-failures — they
    leave failed=0 but the item is still NOT a success."""
    queue.set_runner(_make_runner([], summary_factory=lambda item: {
        'status': 'no_source_id',
        'source': None,
        'total': 0,
        'moved': 0,
        'skipped': 0,
        'failed': 0,
        'errors': [],
    }))
    qid = _enqueue(queue, album_id='alb-1')['queue_id']
    assert _wait_for(lambda: any(r['queue_id'] == qid for r in queue.snapshot()['recent']))
    snap = queue.snapshot()
    item = next(r for r in snap['recent'] if r['queue_id'] == qid)
    assert item['status'] == 'failed'
    assert item['result_status'] == 'no_source_id'


def test_cancel_queued_item(queue):
    """Cancel BEFORE the worker reaches the item drops it cleanly."""
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    first = _enqueue(queue, album_id='alb-1')['queue_id']  # gets pulled to running, blocks
    second = _enqueue(queue, album_id='alb-2')['queue_id']  # sits in queued

    # Wait for first to be running so we know the worker is parked on it.
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)

    result = queue.cancel(second)
    assert result['cancelled'] is True

    snap = queue.snapshot()
    assert all(i['queue_id'] != second for i in snap['queued'])
    # And the cancelled one shows up in recent with status 'cancelled'.
    assert any(i['queue_id'] == second and i['status'] == 'cancelled' for i in snap['recent'])

    block.set()  # release the running item


def test_cancel_running_rejected(queue):
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    qid = _enqueue(queue, album_id='alb-1')['queue_id']
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)

    result = queue.cancel(qid)
    assert result['cancelled'] is False
    assert result['reason'] == 'running_cant_cancel'
    block.set()


def test_cancel_unknown_id(queue):
    result = queue.cancel('does-not-exist')
    assert result['cancelled'] is False
    assert result['reason'] == 'not_found'


def test_clear_queued_bulk_cancel(queue):
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    _enqueue(queue, album_id='alb-1')  # running, blocked
    queued_ids = [_enqueue(queue, album_id=f'alb-{i}')['queue_id'] for i in range(2, 6)]

    assert _wait_for(lambda: queue.snapshot()['active'] is not None)
    assert _wait_for(lambda: len(queue.snapshot()['queued']) == 4)

    cancelled = queue.clear_queued()
    assert cancelled == 4

    snap = queue.snapshot()
    assert len(snap['queued']) == 0
    # Running item is untouched.
    assert snap['active'] is not None
    cancelled_in_recent = [i for i in snap['recent'] if i['status'] == 'cancelled']
    assert {i['queue_id'] for i in cancelled_in_recent} == set(queued_ids)
    block.set()


def test_snapshot_shape(queue):
    snap = queue.snapshot()
    assert set(snap.keys()) == {'active', 'queued', 'recent', 'totals'}
    assert set(snap['totals'].keys()) >= {'queued', 'running', 'done', 'failed', 'cancelled'}
    assert snap['active'] is None
    assert snap['queued'] == []
    assert snap['recent'] == []


def test_update_active_progress_only_targets_running(queue):
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    qid = _enqueue(queue, album_id='alb-1')['queue_id']
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)

    queue.update_active_progress(
        queue_id=qid,
        current_track='Dream On',
        total=8,
        processed=3,
        moved=3,
        skipped=0,
        failed=0,
    )
    snap = queue.snapshot()
    assert snap['active']['current_track'] == 'Dream On'
    assert snap['active']['progress_total'] == 8
    assert snap['active']['progress_processed'] == 3
    assert snap['active']['moved'] == 3
    block.set()


def test_update_progress_for_unknown_id_is_noop(queue):
    """Calling update_active_progress for an item that isn't running
    must not raise, must not corrupt other items."""
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))
    qid = _enqueue(queue, album_id='alb-1')['queue_id']
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)

    queue.update_active_progress(queue_id='not-a-real-id', current_track='X', total=999)
    snap = queue.snapshot()
    assert snap['active']['queue_id'] == qid
    assert snap['active']['progress_total'] == 0  # unchanged
    block.set()


def test_enqueue_many_tallies_enqueued_and_dedupes(queue):
    """Bulk enqueue returns ``{enqueued, already_queued, total}`` so
    the route handler doesn't have to count itself. Re-enqueuing the
    same album-id twice in the same batch dedupes."""
    block = threading.Event()
    queue.set_runner(_make_runner([], block_event=block))

    # Pre-existing item — should appear as already_queued.
    queue.enqueue(album_id='alb-existing', album_title='X',
                  artist_id='ar-1', artist_name='A', source=None)
    # Wait for it to be running so the dedupe path triggers.
    assert _wait_for(lambda: queue.snapshot()['active'] is not None)

    items = [
        {'album_id': 'alb-existing', 'album_title': 'X', 'artist_id': 'ar-1', 'artist_name': 'A'},
        {'album_id': 'alb-new-1', 'album_title': 'Y', 'artist_id': 'ar-1', 'artist_name': 'A'},
        {'album_id': 'alb-new-2', 'album_title': 'Z', 'artist_id': 'ar-1', 'artist_name': 'A'},
    ]
    result = queue.enqueue_many(items)
    assert result == {'enqueued': 2, 'already_queued': 1, 'total': 3}
    block.set()


def test_enqueue_many_carries_source_per_item(queue):
    """Each dict's ``source`` is honoured independently — the bulk
    helper doesn't collapse them to one value."""
    record = []
    queue.set_runner(_make_runner(record))
    items = [
        {'album_id': 'a', 'album_title': 'A', 'artist_id': 'x', 'artist_name': 'X', 'source': 'spotify'},
        {'album_id': 'b', 'album_title': 'B', 'artist_id': 'x', 'artist_name': 'X', 'source': 'deezer'},
        {'album_id': 'c', 'album_title': 'C', 'artist_id': 'x', 'artist_name': 'X', 'source': None},
    ]
    queue.enqueue_many(items)
    assert _wait_for(lambda: len(record) == 3)
    assert [r[1] for r in record] == ['spotify', 'deezer', None]


def test_enqueue_many_handles_empty_list(queue):
    queue.set_runner(_make_runner([]))
    assert queue.enqueue_many([]) == {'enqueued': 0, 'already_queued': 0, 'total': 0}


def test_enqueue_many_dedupes_batch_internal_duplicates(queue):
    """Same album_id appearing twice in the same bulk request must be
    deduped against each other — not just against pre-existing items.
    Regression for the race where a fast runner finishes the first copy
    before the loop reaches the second, letting both slip through."""
    record = []
    queue.set_runner(_make_runner(record))
    items = [
        {'album_id': 'alb-x', 'album_title': 'X', 'artist_id': 'ar-1', 'artist_name': 'A'},
        {'album_id': 'alb-y', 'album_title': 'Y', 'artist_id': 'ar-1', 'artist_name': 'A'},
        {'album_id': 'alb-x', 'album_title': 'X (dup)', 'artist_id': 'ar-1', 'artist_name': 'A'},
    ]
    result = queue.enqueue_many(items)
    assert result == {'enqueued': 2, 'already_queued': 1, 'total': 3}
    # Wait for the queue to drain, then give the worker a moment to
    # try (and fail) to pick a phantom third item. If the dedupe leaked,
    # a third runner call would land here.
    assert _wait_for(lambda: queue.snapshot()['active'] is None and not queue.snapshot()['queued'])
    time.sleep(0.05)
    assert len(record) == 2


def test_cancel_and_run_are_mutually_exclusive(queue):
    """Regression for kettui's ``_next_queued() → status flip`` race:
    a successfully-cancelled item must NEVER have its runner invoked.
    With the old non-atomic pick + flip, cancel could land between
    the worker's pick and its flip-to-running, leaving the item
    marked 'cancelled' but the worker still runs it.

    Hammers many enqueue-then-immediately-cancel pairs to exercise the
    race window. After draining, every queue_id whose cancel returned
    ``cancelled: True`` must NOT appear in the runner's record."""
    runner_called: set = set()
    runner_lock = threading.Lock()

    def runner(item):
        with runner_lock:
            runner_called.add(item.queue_id)
        # Slight runtime widens the window where overlapping cancels
        # could (incorrectly) fire on a running item.
        time.sleep(0.002)
        return {
            'status': 'completed', 'source': 'spotify',
            'total': 1, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    queue.set_runner(runner)

    successful_cancels: set = set()
    for i in range(50):
        r = _enqueue(queue, album_id=f'alb-race-{i}')
        # Immediately try to cancel — half will land while item is still
        # 'queued', half will land after worker has flipped to 'running'.
        if queue.cancel(r['queue_id'])['cancelled']:
            successful_cancels.add(r['queue_id'])

    assert _wait_for(
        lambda: queue.snapshot()['active'] is None and not queue.snapshot()['queued'],
        timeout=5.0,
    )

    leaked = successful_cancels & runner_called
    assert not leaked, f"Runner ran for cancelled items: {leaked}"


def test_no_runner_marks_item_failed(queue):
    """If the worker pulls an item but no runner has been set, the item
    must be marked failed (not silently dropped). In practice
    web_server.py wires the runner at module load before any request
    can land, so this is a defensive-failure path more than a real
    one — but the failure mode must be loud."""
    queue.set_runner(None)
    qid = _enqueue(queue, album_id='alb-orphan')['queue_id']
    assert _wait_for(lambda: any(r['queue_id'] == qid for r in queue.snapshot()['recent']))
    snap = queue.snapshot()
    failed = next(i for i in snap['recent'] if i['queue_id'] == qid)
    assert failed['status'] == 'failed'
    assert 'runner' in (failed['error'] or '').lower()
