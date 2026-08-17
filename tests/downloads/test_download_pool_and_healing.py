"""Three things found while diagnosing Sokhi's "stuck on processing" report.

None of them were his actual bug (that was post-processing queueing behind
searches, fixed separately). They came out of reading the code around it.

1. **"Concurrent Downloads" stopped meaning anything above 3.** The setting
   offers 1,2,3,4,5,6,8,10 and is a PER BATCH limit, but the worker pool was a
   hardwired ``max_workers=3``. So a batch would start 10 workers and 7 sat in
   the executor queue. Measured: settings 4,5,6,8,10 all gave a peak of 3
   running at once, while the help text promised "higher values speed up large
   playlists and wishlists".

2. **A batch wedged in post_processing had no rescue.** The Safety Valve covers
   searching / queued / downloading only. The 30-minute post_processing timeout
   lives inside ``check_batch_completion_v2``, which batch healing only called
   when the batch had ORPHANS — and a batch whose remaining tasks are all
   post_processing produces none. So nothing ever asked, and it held its slots.

3. **Orphans were re-reported forever.** Healing found finished tasks still in
   ``batch['queue']`` and never removed them, so the same ones came back every
   30s. Sokhi's log has the identical 13 re-reported across the whole window.
   They are still not removed — ``queue_index`` is a POSITION into that list and
   shrinking it underneath would make the batch skip or repeat tasks — but they
   are only reported and acted on once.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest


def _source(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# ── 1. the pool follows the setting ─────────────────────────────────────────

@pytest.fixture
def pool_size(monkeypatch):
    """web_server's sizing function with the config stubbed."""
    import web_server

    def _with(value):
        monkeypatch.setattr(web_server, 'config_manager', type('C', (), {
            'get': staticmethod(lambda key, default=None:
                                value if key == 'download_source.max_concurrent' else default),
        })())
        return web_server._download_pool_size()
    return _with


@pytest.mark.parametrize("setting,expected", [
    (1, 3), (2, 3), (3, 3),          # floor: never shrink below today's default
    (4, 4), (5, 5), (6, 6), (8, 8), (10, 10),   # every value the dropdown offers
])
def test_the_pool_follows_the_concurrent_downloads_setting(pool_size, setting, expected):
    assert pool_size(setting) == expected


def test_a_tiny_setting_does_not_serialize_every_other_batch(pool_size):
    """1 and 2 are honoured per batch by ``batch['max_concurrent']``. Shrinking
    the shared pool as well would make one careful batch throttle all the rest."""
    assert pool_size(1) == 3


@pytest.mark.parametrize("junk", [None, '', 'lots', 0, -5, [3]])
def test_a_nonsense_setting_falls_back_to_the_default(pool_size, junk):
    assert pool_size(junk) == 3


def test_a_hand_edited_config_cannot_spawn_hundreds(pool_size):
    """The dropdown stops at 10, but config.json is editable by hand and the
    pool is a process-wide resource."""
    assert pool_size(5000) == 16


def test_the_pool_is_actually_built_from_it():
    src = _source('web_server.py')
    assert 'ThreadPoolExecutor(max_workers=_download_pool_size()' in src, (
        "the sizing function has to reach the pool, or the setting still lies")


def test_the_help_text_no_longer_promises_something_it_cannot_do():
    """It said higher values speed things up, which was false for 4 through 10.
    Now it's true, but only after a restart — the pool is built at import.

    Anchored to THIS setting's own help block. A bare substring search passed
    even with the sentence deleted, because an unrelated setting further down
    the page carries the same phrase.
    """
    html = _source('webui/index.html')
    block = html[html.index('id="max-concurrent-downloads"'):]
    block = block[:block.index('</div>', block.index('setting-help-body'))]
    assert 'Takes effect after a restart' in block, (
        "the Concurrent Downloads help text must say the pool is built at startup")


def test_more_threads_really_do_run_more_at_once():
    """The claim the setting makes, measured rather than assumed."""
    import threading

    def peak(pool_size, submitted):
        pool = ThreadPoolExecutor(max_workers=pool_size)
        live = pk = 0
        lock = threading.Lock()

        def work():
            nonlocal live, pk
            with lock:
                live += 1
                pk = max(pk, live)
            time.sleep(0.05)
            with lock:
                live -= 1
        for f in [pool.submit(work) for _ in range(submitted)]:
            f.result()
        pool.shutdown()
        return pk

    assert peak(3, 10) == 3, "the old behaviour: 10 asked for, 3 ran"
    assert peak(10, 10) == 10, "the fix: the setting now buys real parallelism"


# ── 2 + 3. healing asks for the rescue at the right times ───────────────────

@pytest.fixture
def heal(monkeypatch):
    """Runs the REAL ``validate_and_heal_batch_states`` over a batch you
    describe, and reports which batches it asked to complete.

    Deliberately not a re-implementation of the loop: a mirror passes happily
    while the real thing rots.
    """
    import web_server
    from core.runtime_state import download_batches, download_tasks

    checked = []
    monkeypatch.setattr(web_server, '_check_batch_completion_v2', checked.append)
    monkeypatch.setattr(web_server, '_start_next_batch_of_downloads', lambda _b: None)

    download_batches.clear()
    download_tasks.clear()
    batch = {'queue': [], 'queue_index': 0, 'active_count': 0,
             'max_concurrent': 3, 'phase': 'downloading'}
    download_batches['b1'] = batch

    def _run(statuses, pp_age=0):
        now = time.time()
        batch['queue'] = []
        for i, status in enumerate(statuses):
            tid = f"t{i}"
            download_tasks[tid] = {
                'status': status, 'track_index': i, 'batch_id': 'b1',
                'status_change_time': now - (pp_age if status == 'post_processing' else 0),
            }
            batch['queue'].append(tid)
        batch['queue_index'] = len(batch['queue'])
        batch['active_count'] = sum(
            s in ('searching', 'downloading', 'queued', 'post_processing') for s in statuses)
        checked.clear()
        web_server.validate_and_heal_batch_states()
        return list(checked)

    try:
        yield _run
    finally:
        download_batches.clear()
        download_tasks.clear()


def test_a_batch_wedged_entirely_in_post_processing_now_gets_the_rescue(heal):
    """The hole. No orphans, so nothing used to ask, so the 30-minute timeout
    inside the completion check never ran and the slots were held forever."""
    assert heal(['post_processing'] * 3, pp_age=2000) == ['b1']


def test_a_batch_still_inside_the_window_is_left_alone(heal):
    """30 minutes is a grace period, not a target — a slow AcoustID pass must
    not be cut short."""
    assert heal(['post_processing'] * 3, pp_age=60) == []


def test_a_new_orphan_still_asks_for_the_check(heal):
    """The path that already worked stays working."""
    assert heal(['post_processing', 'failed']) == ['b1']


def test_the_same_orphan_is_not_reported_twice(heal):
    """Sokhi's log: the identical 13 orphans every 30s for the whole window,
    burying the real events and re-running the check for nothing."""
    assert heal(['failed', 'not_found']) == ['b1'], "first sighting acts"
    assert heal(['failed', 'not_found']) == [], "second sighting is silent"


def test_a_stuck_task_asks_again_even_when_the_orphans_are_old_news(heal):
    """Suppressing repeat orphans must not suppress the new rescue — that would
    trade one silent stall for another."""
    assert heal(['failed', 'not_found']) == ['b1']
    assert heal(['failed', 'not_found', 'post_processing'], pp_age=2000) == ['b1']


def test_a_healthy_busy_batch_is_never_disturbed(heal):
    """Nothing finished, nothing wedged — healing must stay out of the way."""
    assert heal(['searching', 'downloading', 'queued']) == []


# ── the wiring, which the logic tests above cannot see ──────────────────────

def test_healing_uses_the_timeout_lifecycle_defines():
    """One definition. Healing decides WHEN to ask; lifecycle owns the window
    and what to do about it."""
    src = _source('web_server.py')
    assert 'from core.downloads.lifecycle import _POST_PROCESSING_STUCK_TIMEOUT' in src
    assert '_pp_age > _POST_PROCESSING_STUCK_TIMEOUT' in src


def test_healing_triggers_on_stuck_post_processing_too():
    src = _source('web_server.py')
    assert 'if _new_orphans or stuck_post_processing:' in src


def test_orphans_are_still_never_dropped_from_the_queue():
    """Deliberate. ``queue_index`` is a position into ``batch['queue']``, so
    removing entries would make the batch skip or repeat tasks. Suppressing the
    repeat REPORT is the safe half of the fix."""
    src = _source('web_server.py')
    fn = src[src.index('def validate_and_heal_batch_states'):]
    fn = fn[:fn.index('\ndef ', 10)]
    assert "_reported_orphans" in fn
    for bad in ("queue.remove(", "queue'].remove(", "orphaned_tasks.remove("):
        assert bad not in fn, f"{bad} would corrupt queue_index"
