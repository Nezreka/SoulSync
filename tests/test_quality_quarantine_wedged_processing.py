"""Quality/audio-guard quarantine through the verification wrapper must fail
or retry the task — never leave it wedged in post_processing.

Sokhi's report (Discord, Aug 16, Docker 3.2.2): every download quarantined
(profile wanted FLAC 24/192, sources served 16/44) and the task then sat in
"Processing" until Stuck Detection V2 reaped it at 1800s — half an hour of a
wedged download slot per quarantine, eight times in one 500-line log, starving
the whole queue.

The mechanism: ``post_process_matched_download_with_verification`` pops
task_id/batch_id OUT of the context before running the inner pipeline (so the
inner batch accounting stays wrapper-owned). The inner quality/silence branches
then read ``context.get('task_id')`` — always None through the wrapper — so
their requeue never fired and their failed-marking no-op'd. The wrapper's old
code just returned on the claim that "the inner pipeline already handled
retry/fail", which is only ever true for direct callers. Nobody moved the task
out of post_processing.

The fix: the wrapper now does the requeue-first / fail-second dance itself,
with the REAL ids it held all along — the same stash-and-apply pattern
``_mark_task_quarantined`` already used for the quarantine entry id.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

import core.imports.pipeline as import_pipeline
import core.runtime_state as runtime_state


@pytest.fixture
def _isolate_state():
    snapshot = {
        'tasks': dict(runtime_state.download_tasks),
        'batches': dict(runtime_state.download_batches),
        'matched_ctx': dict(runtime_state.matched_downloads_context),
    }
    runtime_state.download_tasks.clear()
    runtime_state.download_batches.clear()
    runtime_state.matched_downloads_context.clear()
    yield
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks.update(snapshot['tasks'])
    runtime_state.download_batches.clear()
    runtime_state.download_batches.update(snapshot['batches'])
    runtime_state.matched_downloads_context.clear()
    runtime_state.matched_downloads_context.update(snapshot['matched_ctx'])


def _build_runtime(completion_calls):
    return types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda batch, task, success: completion_calls.append(
            (batch, task, success)
        ),
        web_scan_manager=None,
        repair_worker=None,
    )


def _seed_task(task_id: str, batch_id: str) -> None:
    runtime_state.download_tasks[task_id] = {
        'task_id': task_id,
        'batch_id': batch_id,
        'status': 'post_processing',
        'track_info': {'name': 'Ref:rain'},
    }


def _run_wrapper(context, task_id, batch_id, runtime, inner, requeue):
    with patch.object(import_pipeline, 'post_process_matched_download', inner), \
         patch.object(import_pipeline, '_requeue_quarantined_task_for_retry', requeue):
        import_pipeline.post_process_matched_download_with_verification(
            context['context_key'], context, '/fake/source.flac',
            task_id, batch_id, runtime,
        )


def test_quality_quarantine_without_retry_marks_task_failed(_isolate_state):
    """The Sokhi wedge: quarantine, no retry candidate — the task must land in
    'failed' with the stashed reason, and the batch must be notified. Before
    the fix it stayed in post_processing until the 1800s reaper."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)
    _seed_task('t1', 'b1')
    context = {'task_id': 't1', 'batch_id': 'b1', 'context_key': 'test::c1'}

    def _inner(*a, **kw):
        # What the real inner pipeline does through the wrapper: it has NO
        # task_id in the context (popped), so all it can do is set the flags.
        assert context.get('task_id') is None, \
            "wrapper stopped popping task_id — this test's premise changed"
        context['_bitdepth_rejected'] = True
        context['_quarantine_reject_reason'] = \
            'Quality filter: file is FLAC 16-bit/44kHz, does not satisfy any configured target'

    requeue_calls = []

    def _requeue(task_id, batch_id, trigger):
        requeue_calls.append((task_id, batch_id, trigger))
        return False

    _run_wrapper(context, 't1', 'b1', runtime, _inner, _requeue)

    # The requeue was attempted with the REAL ids, not the popped Nones.
    assert requeue_calls == [('t1', 'b1', 'quality')]
    assert runtime_state.download_tasks['t1']['status'] == 'failed'
    assert 'Quality filter' in runtime_state.download_tasks['t1']['error_message']
    assert ('b1', 't1', False) in completion_calls


def test_silence_quarantine_without_retry_marks_task_failed(_isolate_state):
    completion_calls = []
    runtime = _build_runtime(completion_calls)
    _seed_task('t2', 'b2')
    context = {'task_id': 't2', 'batch_id': 'b2', 'context_key': 'test::c2'}

    def _inner(*a, **kw):
        context['_silence_rejected'] = True
        context['_quarantine_reject_reason'] = 'Audio guard: decoded to 0.4s of silence'

    def _requeue(task_id, batch_id, trigger):
        assert trigger == 'silence'
        return False

    _run_wrapper(context, 't2', 'b2', runtime, _inner, _requeue)

    assert runtime_state.download_tasks['t2']['status'] == 'failed'
    assert 'Audio guard' in runtime_state.download_tasks['t2']['error_message']
    assert ('b2', 't2', False) in completion_calls


def test_successful_requeue_leaves_the_task_alive(_isolate_state):
    """Requeue-first, fail-second: when the monitor accepts the retry, the task
    belongs to the next candidate now — marking it failed would clobber a live
    retry, and the batch must NOT be told it finished."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)
    _seed_task('t3', 'b3')
    context = {'task_id': 't3', 'batch_id': 'b3', 'context_key': 'test::c3'}

    def _inner(*a, **kw):
        context['_bitdepth_rejected'] = True
        context['_quarantine_reject_reason'] = 'Quality filter: nope'

    _run_wrapper(context, 't3', 'b3', runtime, _inner, lambda *a: True)

    assert runtime_state.download_tasks['t3']['status'] != 'failed'
    assert completion_calls == []


def test_missing_reason_still_fails_with_a_generic_message(_isolate_state):
    """The reason stash is new — an inner pipeline that predates it (or a
    direct flag set in a test) must still produce a failed task, not a wedge."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)
    _seed_task('t4', 'b4')
    context = {'task_id': 't4', 'batch_id': 'b4', 'context_key': 'test::c4'}

    def _inner(*a, **kw):
        context['_bitdepth_rejected'] = True   # no _quarantine_reject_reason

    _run_wrapper(context, 't4', 'b4', runtime, _inner, lambda *a: False)

    assert runtime_state.download_tasks['t4']['status'] == 'failed'
    assert runtime_state.download_tasks['t4']['error_message']
    assert ('b4', 't4', False) in completion_calls
