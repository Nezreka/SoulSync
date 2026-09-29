"""M13/M14: pipeline discovery phase must not declare success when the worker
times out or raises — the phase result must carry the real outcome."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from core.playlists import pipeline


class _FakeDb:
    def get_mirrored_playlists(self, *a):
        return [{'id': 1, 'name': 'PL', 'source': 'spotify'}]


class _Deps:
    def __init__(self, worker):
        self._worker = worker
        self.progress_calls = []
        self.logger = logging.getLogger('test-pipeline')

    def update_progress(self, automation_id, **kwargs):
        self.progress_calls.append(kwargs)

    def run_playlist_discovery_worker(self, playlists, automation_id=None):
        return self._worker(playlists, automation_id)


def _run_phase(deps, **overrides):
    return pipeline._run_discovery_phase(
        deps, 'auto-1', db=_FakeDb(), playlist_id=None, process_all=True, **overrides
    )


def _final_progress(deps):
    phase_ends = [c for c in deps.progress_calls if 'phase' in c and ('complete' in c['phase'] or 'timed out' in c['phase'] or 'failed' in c['phase'])]
    return phase_ends[-1] if phase_ends else {}


# ---------------------------------------------------------------------------
# M14: worker exceptions must surface in the phase result
# ---------------------------------------------------------------------------


def test_worker_exception_is_recorded_not_swallowed():
    """M14: a discovery worker that raises must be reported as failed —
    never as 'Discovery complete' with log_type='success'."""
    def boom(playlists, automation_id=None):
        raise RuntimeError('provider exploded')

    deps = _Deps(boom)
    result = _run_phase(deps)

    assert result is not None
    assert result['status'] == 'failed'
    assert 'provider exploded' in result['error']
    final = _final_progress(deps)
    assert 'failed' in final['phase'].lower()
    assert final['log_type'] == 'error'


def test_worker_exception_does_not_claim_success():
    def boom(playlists, automation_id=None):
        raise RuntimeError('x')

    deps = _Deps(boom)
    result = _run_phase(deps)

    assert result is not None
    assert result['status'] != 'completed'
    assert not any(
        c.get('log_type') == 'success' and 'complete' in str(c.get('phase', '')).lower()
        for c in deps.progress_calls
    )


# ---------------------------------------------------------------------------
# M13: timeout must mark the phase timed-out and join the worker
# ---------------------------------------------------------------------------


def test_timeout_marks_phase_timed_out_not_complete(monkeypatch):
    """M13: on timeout the phase must report timed-out (not 'Discovery
    complete') so the caller doesn't proceed to sync under a false success."""
    worker_done = threading.Event()

    def slow_worker(playlists, automation_id=None):
        time.sleep(30)
        worker_done.set()

    monkeypatch.setattr(pipeline, 'DISCOVERY_TIMEOUT_SECONDS', 1)
    monkeypatch.setattr(pipeline, 'DISCOVERY_TIMEOUT_GRACE_SECONDS', 1, raising=False)

    deps = _Deps(slow_worker)
    result = _run_phase(deps)

    assert result is not None
    assert result['status'] == 'timed_out'
    final = _final_progress(deps)
    assert 'timed out' in final['phase'].lower()
    assert final['log_type'] == 'error'
    assert not any(
        c.get('phase') == 'Phase 2/4: Discovery complete'
        for c in deps.progress_calls
    )


def test_timeout_joins_worker_before_returning(monkeypatch):
    """M13: after the timeout the phase must join the worker (grace period)
    before returning, so sync doesn't start while discovery still mutates
    playlist metadata."""
    worker_done = threading.Event()

    def slowish_worker(playlists, automation_id=None):
        # Sleeps longer than the 3s poll quantum (so the timeout fires
        # deterministically) but shorter than the 10s grace period (so the
        # join observes completion). A 3s sleep raced the 3s poll quantum.
        time.sleep(6)
        worker_done.set()

    monkeypatch.setattr(pipeline, 'DISCOVERY_TIMEOUT_SECONDS', 1)
    monkeypatch.setattr(pipeline, 'DISCOVERY_TIMEOUT_GRACE_SECONDS', 10, raising=False)

    deps = _Deps(slowish_worker)
    result = _run_phase(deps)

    assert result is not None
    assert result['status'] == 'timed_out'
    # the worker finished inside the grace join — it cannot still be running
    assert worker_done.is_set()


# ---------------------------------------------------------------------------
# happy path stays green
# ---------------------------------------------------------------------------


def test_successful_worker_still_reports_completed():
    def ok_worker(playlists, automation_id=None):
        return None

    deps = _Deps(ok_worker)
    result = _run_phase(deps)

    assert result is not None
    assert result['status'] == 'completed'
    final = _final_progress(deps)
    assert final['phase'] == 'Phase 2/4: Discovery complete'
    assert final['log_type'] == 'success'
