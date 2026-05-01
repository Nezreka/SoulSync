"""Tests for core/downloads/post_processing.py — verification worker for completed downloads.

The worker is large + side-effecty. Tests cover the major control-flow
branches: missing task, cancelled, already-completed, missing
filename/username, file-found-in-transfer with + without metadata, file-
found-in-downloads with + without context, file-not-found-after-retries,
youtube special path, and top-level exception swallow.
"""

from __future__ import annotations

import os
import pytest

from core.downloads import post_processing as pp
from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Recorder:
    """Captures every call into a list of (name, args, kwargs)."""
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def _inner(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _inner


def _build_deps(
    *,
    config=None,
    soulseek_client=None,
    run_async=None,
    docker_resolve_path=None,
    extract_filename=None,
    make_context_key=None,
    find_completed_file=None,
    enhance_file_metadata=None,
    wipe_source_tags=None,
    post_process_with_verification=None,
    mark_task_completed=None,
    on_download_completed=None,
):
    rec = _Recorder()
    return pp.PostProcessDeps(
        config_manager=config or _FakeConfig(),
        soulseek_client=soulseek_client,
        run_async=run_async or (lambda c: None),
        docker_resolve_path=docker_resolve_path or (lambda p: p),
        extract_filename=extract_filename or (lambda f: os.path.basename(f) if f else ''),
        make_context_key=make_context_key or (lambda u, f: f"{u}::{f}"),
        find_completed_file=find_completed_file or (lambda *a, **kw: (None, None)),
        enhance_file_metadata=enhance_file_metadata or rec('enhance'),
        wipe_source_tags=wipe_source_tags or rec('wipe'),
        post_process_with_verification=post_process_with_verification or rec('post_process'),
        mark_task_completed=mark_task_completed or rec('mark_completed'),
        on_download_completed=on_download_completed or rec('on_complete'),
    ), rec


class _FakeConfig:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


# ---------------------------------------------------------------------------
# Branch coverage tests
# ---------------------------------------------------------------------------

def test_missing_task_returns_early_no_callbacks():
    deps, rec = _build_deps()
    pp.run_post_processing_worker('absent', 'b1', deps)
    assert rec.calls == []


def test_cancelled_task_returns_early_no_callbacks():
    download_tasks['t1'] = {'status': 'cancelled'}
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert rec.calls == []


def test_already_completed_task_returns_early():
    download_tasks['t1'] = {'status': 'completed'}
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert rec.calls == []


def test_stream_processed_task_returns_early():
    download_tasks['t1'] = {'status': 'post_processing', 'stream_processed': True}
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert rec.calls == []


def test_missing_filename_marks_failed_and_calls_on_complete():
    download_tasks['t1'] = {'status': 'post_processing', 'username': 'u1', 'track_info': {}}
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'Post-processing failed' in download_tasks['t1']['error_message']
    assert ('on_complete', ('b1', 't1', False), {}) in rec.calls


def test_missing_username_marks_failed_and_calls_on_complete():
    download_tasks['t1'] = {'status': 'post_processing', 'filename': 'song.flac', 'track_info': {}}
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'


def test_file_not_found_after_retries_marks_failed(monkeypatch):
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {},
    }
    # Skip sleeps to keep test fast
    monkeypatch.setattr(pp.time, 'sleep', lambda s: None)
    deps, rec = _build_deps()
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'File not found on disk' in download_tasks['t1']['error_message']
    assert ('on_complete', ('b1', 't1', False), {}) in rec.calls


def test_stream_processor_completes_during_search_loop_returns_no_failure(monkeypatch):
    """If task gets marked completed by stream processor mid-retry, abort without failing."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {},
    }
    monkeypatch.setattr(pp.time, 'sleep', lambda s: None)
    call_count = [0]

    def _stream_completes_after_first_search(*a, **kw):
        call_count[0] += 1
        if call_count[0] >= 1:
            download_tasks['t1']['stream_processed'] = True
        return (None, None)

    deps, rec = _build_deps(find_completed_file=_stream_completes_after_first_search)
    pp.run_post_processing_worker('t1', 'b1', deps)
    # Worker should detect stream_processed, return early, not mark failed
    assert download_tasks['t1']['status'] == 'post_processing'  # original status preserved
    assert ('on_complete', ('b1', 't1', False), {}) not in rec.calls


def test_file_found_in_transfer_with_metadata_enhanced_skips_enhancement_and_completes():
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {'name': 'Money'},
        'metadata_enhanced': True,
    }
    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: ('/transfer/song.flac', 'transfer'),
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    # No enhance call because metadata_enhanced=True
    assert not any(c[0] == 'enhance' for c in rec.calls)
    # Mark + on-complete called
    assert any(c[0] == 'mark_completed' for c in rec.calls)
    assert ('on_complete', ('b1', 't1', True), {}) in rec.calls


def test_file_found_in_transfer_no_context_no_filename_wipes_tags(monkeypatch):
    """Transfer file but missing context AND expected filename -> wipe tags only."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {},
        'metadata_enhanced': False,
    }
    monkeypatch.setattr(pp.os.path, 'exists', lambda p: True)
    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: ('/transfer/song.flac', 'transfer'),
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    # wipe_source_tags called (no full enhancement possible)
    assert any(c[0] == 'wipe' for c in rec.calls)
    # Still completed
    assert ('on_complete', ('b1', 't1', True), {}) in rec.calls


def test_file_found_in_downloads_with_context_runs_post_process_with_verification():
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {'name': 'Money'},
    }
    matched_downloads_context['u1::song.flac'] = {
        'original_search_result': {'title': 'Money', 'track_number': 1},
        'context_artist': {'name': 'Pink Floyd', 'id': 'art1'},
        'context_album': {'name': 'DSOTM'},
    }
    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: ('/downloads/song.flac', 'download'),
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    # post_process_with_verification called with the context + file
    assert any(c[0] == 'post_process' for c in rec.calls)


def test_file_found_in_downloads_no_context_marks_completed_directly():
    """No matched context for the file → just mark completed since file exists."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {'name': 'Money'},
    }
    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: ('/downloads/song.flac', 'download'),
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    # No post_process call (no context)
    assert not any(c[0] == 'post_process' for c in rec.calls)
    # Mark + on-complete called
    assert any(c[0] == 'mark_completed' for c in rec.calls)
    assert ('on_complete', ('b1', 't1', True), {}) in rec.calls


def test_processing_exception_marks_failed_and_calls_on_complete():
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {'name': 'Money'},
    }
    matched_downloads_context['u1::song.flac'] = {'original_search_result': {}}

    def _exploding_post_process(*a, **kw):
        raise RuntimeError("post-process boom")

    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: ('/downloads/song.flac', 'download'),
        post_process_with_verification=_exploding_post_process,
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'Post-processing failed' in download_tasks['t1']['error_message']
    assert ('on_complete', ('b1', 't1', False), {}) in rec.calls


def test_critical_outer_exception_marks_failed():
    """Top-level exception (e.g. broken deps) still marks task failed."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {},
    }

    def _broken_resolve(p):
        raise RuntimeError("config dead")

    deps, rec = _build_deps(docker_resolve_path=_broken_resolve)
    # Must NOT raise
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'Critical post-processing error' in download_tasks['t1']['error_message']
    assert ('on_complete', ('b1', 't1', False), {}) in rec.calls


def test_youtube_task_uses_get_download_status_to_resolve_path(monkeypatch):
    """YouTube downloads use a different filename scheme — worker queries soulseek client for real path."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'vid_id||Money',
        'username': 'youtube',
        'download_id': 'dl-yt-1',
        'track_info': {},
    }

    class _FakeStatus:
        file_path = '/downloads/Money.mp3'

    class _FakeYTClient:
        def get_download_status(self, dl_id):
            assert dl_id == 'dl-yt-1'
            return _FakeStatus()

    # File exists on disk (mock)
    monkeypatch.setattr(pp.os.path, 'exists', lambda p: p == '/downloads/Money.mp3')

    deps, rec = _build_deps(
        soulseek_client=_FakeYTClient(),
        run_async=lambda coro: coro,  # not async — direct call
    )
    pp.run_post_processing_worker('t1', 'b1', deps)
    # mark_completed should fire (file resolved from YouTube status)
    assert any(c[0] == 'mark_completed' for c in rec.calls)


def test_fuzzy_context_matching_when_exact_key_missing(monkeypatch):
    """When exact key isn't in matched_downloads_context, worker tries fuzzy match
    constrained to same Soulseek username."""
    download_tasks['t1'] = {
        'status': 'post_processing',
        'filename': 'song.flac',
        'username': 'u1',
        'track_info': {},
    }
    # Different exact key but same user + filename substring
    matched_downloads_context['u1::folder/song.flac'] = {
        'original_search_result': {'title': 'Money', 'track_number': 1},
    }
    deps, rec = _build_deps(
        find_completed_file=lambda *a, **kw: (None, None),  # file not found
    )
    monkeypatch.setattr(pp.time, 'sleep', lambda s: None)
    # Won't find file → marks failed. But the fuzzy match log path executes.
    pp.run_post_processing_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
