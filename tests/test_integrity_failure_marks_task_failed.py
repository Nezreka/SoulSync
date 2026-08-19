"""Pin the contract: integrity rejection must mark the task as failed.

User report (Mr. Morale download): three tracks (Rich Interlude,
Savior Interlude, Savior) showed ✅ Completed in the modal but were
missing from disk. Log trace at line 932 of `core/imports/pipeline.py`
revealed the bug:

    No _final_processed_path in context for task <id> — cannot verify, assuming success

Inner ``post_process_matched_download`` quarantined the source file
(integrity check rejected duration mismatch on a wrong-content file),
which left no ``_final_processed_path`` in the context. The outer
verification wrapper saw no path and fell through to the "assuming
success" branch, marking the task as ✅ Completed even though the file
was in quarantine and would never reach the destination.

Fix: the wrapper handles the explicit quarantine/race markers first and then
requires the inner pipeline's positive success contract. Silence or a file on
disk without a committed Library-v2 file row is not accepted as success.
"""

from __future__ import annotations

import threading
import types
from unittest.mock import patch

import pytest

import core.imports.pipeline as import_pipeline
import core.runtime_state as runtime_state


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolate_state():
    """Snapshot + restore the global runtime maps so this test can mutate
    them without polluting other tests."""
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


def _seed_task(task_id: str = 't1', batch_id: str = 'b1') -> None:
    runtime_state.download_tasks[task_id] = {
        'task_id': task_id,
        'batch_id': batch_id,
        'status': 'downloading',
        'track_info': {'name': 'Rich (Interlude)'},
    }


# ---------------------------------------------------------------------------
# The wrapper-level fix
# ---------------------------------------------------------------------------


def test_integrity_failure_marker_marks_task_failed(_isolate_state):
    """When inner code sets ``_integrity_failure_msg``, the wrapper
    must mark the task failed — NOT fall through to "assume success"."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t1', 'b1')

    context = {
        'task_id': 't1',
        'batch_id': 'b1',
        'context_key': 'test::ctx',
        # Simulate inner code's integrity-rejection state — file went to
        # quarantine, _final_processed_path NEVER got set.
        '_integrity_failure_msg': 'Duration mismatch: file is 163s, expected 152s (drift 11s)',
    }

    # Inner post-processor is a no-op for this test — we're verifying the
    # wrapper-level state machine. Stub everything inside `with_verification`
    # that would otherwise touch real disk / acoustid / etc.
    with patch.object(import_pipeline, 'post_process_matched_download',
                      lambda *a, **kw: None):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx', context, '/fake/source.flac', 't1', 'b1', runtime,
        )

    # Task explicitly marked failed with the integrity error message
    assert runtime_state.download_tasks['t1']['status'] == 'failed'
    assert 'integrity' in runtime_state.download_tasks['t1']['error_message'].lower()
    # Batch tracker notified with success=False
    assert ('b1', 't1', False) in completion_calls
    # Did NOT fall through to "assume success"
    assert ('b1', 't1', True) not in completion_calls


def test_race_guard_failure_marker_marks_task_failed(_isolate_state):
    """Same contract for the race-guard-failed marker (source file
    disappeared with no known destination)."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t2', 'b2')

    context = {
        'task_id': 't2',
        'batch_id': 'b2',
        'context_key': 'test::ctx2',
        '_race_guard_failed': True,
    }

    with patch.object(import_pipeline, 'post_process_matched_download',
                      lambda *a, **kw: None):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx2', context, '/fake/source.flac', 't2', 'b2', runtime,
        )

    assert runtime_state.download_tasks['t2']['status'] == 'failed'
    assert ('b2', 't2', False) in completion_calls


def test_quality_quarantine_does_not_mark_completed(_isolate_state):
    """When the inner pipeline quality-quarantines the file (``_bitdepth_rejected``),
    the wrapper must NOT fall through to "assume success" and mark the task
    Completed — that made the quarantined file appear in BOTH Completed and
    Quarantine. (The wrapper now also owns the retry/fail for this marker —
    the inner ran with task_id popped and could never do it; see
    test_quality_quarantine_wedged_processing.py.)"""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t5', 'b5')

    context = {'task_id': 't5', 'batch_id': 'b5', 'context_key': 'test::ctx5'}

    def _inner(*a, **kw):
        # Inner pipeline quarantined for quality and handled its own retry/fail;
        # it sets the marker and leaves NO _final_processed_path.
        context['_bitdepth_rejected'] = True

    with patch.object(import_pipeline, 'post_process_matched_download', _inner), \
         patch.object(import_pipeline, '_mark_task_completed',
                      lambda task_id, ti: runtime_state.download_tasks[task_id].update(
                          {'status': 'completed'})):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx5', context, '/fake/source.mp3', 't5', 'b5', runtime,
        )

    assert runtime_state.download_tasks['t5']['status'] != 'completed'
    assert ('b5', 't5', True) not in completion_calls


def test_silence_quarantine_does_not_mark_completed(_isolate_state):
    """Same contract for the audio guard (``_silence_rejected``)."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t6', 'b6')

    context = {'task_id': 't6', 'batch_id': 'b6', 'context_key': 'test::ctx6'}

    def _inner(*a, **kw):
        context['_silence_rejected'] = True

    with patch.object(import_pipeline, 'post_process_matched_download', _inner), \
         patch.object(import_pipeline, '_mark_task_completed',
                      lambda task_id, ti: runtime_state.download_tasks[task_id].update(
                          {'status': 'completed'})):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx6', context, '/fake/source.flac', 't6', 'b6', runtime,
        )

    assert runtime_state.download_tasks['t6']['status'] != 'completed'
    assert ('b6', 't6', True) not in completion_calls


def test_no_success_contract_never_assumes_success(_isolate_state):
    """Silence from the inner pipeline is not evidence of a valid import."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t3', 'b3')

    context = {
        'task_id': 't3',
        'batch_id': 'b3',
        'context_key': 'test::ctx3',
        # No failure markers, no _final_processed_path
    }

    with patch.object(import_pipeline, 'post_process_matched_download',
                      lambda *a, **kw: None), \
         patch.object(import_pipeline, '_mark_task_completed',
                      lambda task_id, ti: runtime_state.download_tasks[task_id].update(
                          {'status': 'completed'}
                      )):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx3', context, '/fake/source.flac', 't3', 'b3', runtime,
        )

    assert runtime_state.download_tasks['t3']['status'] == 'failed'
    assert ('b3', 't3', False) in completion_calls
    assert ('b3', 't3', True) not in completion_calls


def test_destination_on_disk_without_library_registration_is_failed(
    _isolate_state, tmp_path,
):
    """A moved file must not hide a failed Library-v2 registration."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)
    _seed_task('t7', 'b7')
    destination = tmp_path / 'Library' / 'Track.flac'
    destination.parent.mkdir()
    destination.write_bytes(b'audio')
    context = {'task_id': 't7', 'batch_id': 'b7'}

    def _inner(*_args, **_kwargs):
        context['_final_processed_path'] = str(destination)
        context['_context_failure_msg'] = 'Library v2 registration failed'

    with patch.object(import_pipeline, 'post_process_matched_download', _inner):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx7', context, '/fake/source.flac', 't7', 'b7', runtime,
        )

    assert runtime_state.download_tasks['t7']['status'] == 'failed'
    assert 'Library v2' in runtime_state.download_tasks['t7']['error_message']
    assert completion_calls == [('b7', 't7', False)]


def test_integrity_failure_takes_priority_over_missing_final_path(_isolate_state):
    """Integrity failure check must run BEFORE the missing-final-path
    fallback. Both conditions are true (no final path AND integrity
    failed); the failure wins."""
    completion_calls = []
    runtime = _build_runtime(completion_calls)

    _seed_task('t4', 'b4')

    context = {
        'task_id': 't4',
        'batch_id': 'b4',
        'context_key': 'test::ctx4',
        '_integrity_failure_msg': 'duration mismatch',
        # no _final_processed_path — would otherwise hit "assume success"
    }

    with patch.object(import_pipeline, 'post_process_matched_download',
                      lambda *a, **kw: None):
        import_pipeline.post_process_matched_download_with_verification(
            'test::ctx4', context, '/fake/source.flac', 't4', 'b4', runtime,
        )

    assert runtime_state.download_tasks['t4']['status'] == 'failed'
    # Critical: must NOT have notified success
    assert ('b4', 't4', True) not in completion_calls
