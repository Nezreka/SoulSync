"""Pin the engine-state fallback that drives non-Soulseek (streaming)
download status forward.

Soulseek downloads land in slskd's ``live_transfers_lookup``, so their
status updates flow through the existing slskd-state branch. Streaming
sources (YouTube, Tidal, Qobuz, HiFi, Deezer, SoundCloud, Lidarr) never
appear there — without these tests' code path, a manually-picked
SoundCloud download stays at "downloading 0%" forever, even after the
engine logs an Errored terminal state.

These tests exercise ``_apply_engine_state_fallback`` directly with a
fake ``download_orchestrator`` so we don't have to spin up the real
engine. The real fix relies on the per-source plugin storing the
terminal state via ``_mark_terminal`` (state='Errored' / 'Completed,
Succeeded'), which our fake mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock

import pytest

from core.downloads import status as status_mod


@dataclass
class _FakeDownloadStatus:
    """Mirror the DownloadStatus shape that engine plugins return — only
    the fields the fallback reads."""
    id: str
    state: str
    progress: float = 0
    error_message: Optional[str] = None


def _make_deps(record_for_id: dict, on_completed=None, submit_pp=None):
    """StatusDeps with a fake orchestrator that returns whatever
    DownloadStatus the test provides for a given download_id."""

    fake_orch = MagicMock()

    async def _fake_get_status(download_id):
        return record_for_id.get(download_id)

    fake_orch.get_download_status = _fake_get_status

    def _sync_run_async(coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    return status_mod.StatusDeps(
        config_manager=MagicMock(),
        docker_resolve_path=lambda p: p,
        find_completed_file=lambda *args, **kwargs: (None, None),
        make_context_key=lambda u, f: f"{u}::{f}",
        submit_post_processing=submit_pp or (lambda task_id, batch_id: None),
        get_cached_transfer_data=lambda: {},
        download_orchestrator=fake_orch,
        run_async=_sync_run_async,
        on_download_completed=on_completed,
    )


def _task(*, status='downloading', username='soundcloud', filename='1234||https://sc/x',
          download_id='dl-1', manual_pick=True):
    """Default to manual_pick=True because the engine fallback is
    deliberately scoped to manual picks — auto attempts go through the
    live_transfers branch + monitor retry path. Tests opt out of the
    flag explicitly when exercising the auto-attempt skip."""
    return {
        'status': status,
        'username': username,
        'filename': filename,
        'download_id': download_id,
        '_user_manual_pick': manual_pick,
        'track_info': {'name': 'Test Track'},
    }


# ---------------------------------------------------------------------------
# Failure / cancel / success transitions
# ---------------------------------------------------------------------------


def test_errored_state_marks_task_failed():
    import time as _t
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    completed_calls = []
    deps = _make_deps(
        {'dl-1': _FakeDownloadStatus(id='dl-1', state='Errored', error_message='HTTP 404')},
        on_completed=lambda batch_id, task_id, success: completed_calls.append((batch_id, task_id, success)),
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'failed'
    assert task['error_message'] == 'HTTP 404'
    assert task_status['status'] == 'failed'
    # on_download_completed is deferred to a daemon thread to avoid the
    # tasks_lock self-deadlock — give it a beat to fire.
    for _ in range(50):
        if completed_calls:
            break
        _t.sleep(0.01)
    assert completed_calls == [('b1', 't1', False)]


def test_compound_completed_errored_hits_failure_branch_first():
    """``"Completed, Errored"`` must be treated as failure, not success.
    Order of state-substring checks matters."""
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps(
        {'dl-1': _FakeDownloadStatus(id='dl-1', state='Completed, Errored')},
        on_completed=lambda *args: None,
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'failed'


def test_cancelled_state_marks_task_cancelled():
    import time as _t
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    completed_calls = []
    deps = _make_deps(
        {'dl-1': _FakeDownloadStatus(id='dl-1', state='Cancelled')},
        on_completed=lambda *args: completed_calls.append(args),
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'cancelled'
    assert task_status['status'] == 'cancelled'
    for _ in range(50):
        if completed_calls:
            break
        _t.sleep(0.01)
    assert len(completed_calls) == 1


def test_succeeded_state_submits_post_processing():
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    pp_calls = []
    deps = _make_deps(
        {'dl-1': _FakeDownloadStatus(id='dl-1', state='Completed, Succeeded', progress=100)},
        submit_pp=lambda task_id, batch_id: pp_calls.append((task_id, batch_id)),
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'post_processing'
    assert task_status['status'] == 'post_processing'
    assert pp_calls == [('t1', 'b1')]


def test_inprogress_reflects_progress_without_changing_status():
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps(
        {'dl-1': _FakeDownloadStatus(id='dl-1', state='InProgress, Downloading', progress=42.5)},
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'
    assert task_status['status'] == 'downloading'
    assert task_status['progress'] == 42.5


# ---------------------------------------------------------------------------
# Gates — bail without mutating state
# ---------------------------------------------------------------------------


def test_skips_when_orchestrator_missing():
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    deps = status_mod.StatusDeps(
        config_manager=MagicMock(),
        docker_resolve_path=lambda p: p,
        find_completed_file=lambda *a, **k: (None, None),
        make_context_key=lambda u, f: f"{u}::{f}",
        submit_post_processing=lambda *a: None,
        get_cached_transfer_data=lambda: {},
        download_orchestrator=None,
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'  # unchanged


def test_skips_terminal_states():
    """Already-failed / completed / cancelled tasks must not be touched —
    they may have been marked by another path (e.g. the live_transfers
    branch on a slskd Errored state for a Soulseek manual pick)."""
    for terminal in ('completed', 'failed', 'cancelled', 'not_found', 'post_processing'):
        task = _task(status=terminal)
        task_status = {'status': terminal, 'progress': 0}
        deps = _make_deps({'dl-1': _FakeDownloadStatus(id='dl-1', state='Errored')})

        status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

        assert task['status'] == terminal


def test_skips_soulseek_username():
    """Soulseek goes through live_transfers_lookup — never the engine
    fallback. Otherwise we'd double-process its terminal state."""
    task = _task(username='peer-username-xyz')  # not in _STREAMING_SOURCE_NAMES
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps({'dl-1': _FakeDownloadStatus(id='dl-1', state='Errored')})

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'


def test_skips_when_download_id_missing():
    task = _task()
    task.pop('download_id')
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps({})

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'


def test_engine_returning_none_leaves_task_alone():
    """Engine doesn't know about this download_id (worker hasn't registered
    yet, or the record was cleaned). The fallback must not falsely mark
    the task failed in this case — the safety valve covers stuck-forever."""
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps({'dl-1': None})

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'


def test_skips_auto_attempts_without_manual_pick_flag():
    """Auto attempts (no _user_manual_pick flag) must NOT hit the engine
    fallback even if they end up in the else branch. The monitor's retry
    path owns auto-attempt failure handling — short-circuiting it here
    would skip the fallback-to-next-candidate behavior."""
    task = _task(manual_pick=False)
    task_status = {'status': 'downloading', 'progress': 0}
    deps = _make_deps({'dl-1': _FakeDownloadStatus(id='dl-1', state='Errored')})

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    # Untouched — auto retry path will handle it.
    assert task['status'] == 'downloading'


# ---------------------------------------------------------------------------
# Live-transfers IF branch — manual-pick failure path
# ---------------------------------------------------------------------------
#
# Streaming-source records are pre-populated into ``live_transfers_lookup``
# via ``download_orchestrator.engine.get_all_downloads(exclude=('soulseek',))``,
# so a manually-picked SoundCloud / YouTube / Tidal / etc. download whose
# engine record reports ``state='Errored'`` arrives via the IF branch
# (lookup_key IS in live_transfers_lookup), NOT the engine-fallback else
# branch. Without the manual-pick guard inside that elif, the live-
# transfers branch would defer to the monitor — which itself bails on
# manual picks — and the task would sit at "downloading 0%" forever.
#
# These tests exercise ``build_batch_status_data`` end-to-end so the
# guard is pinned by behavior rather than by the unit-level fallback
# tests above.


def _seed_runtime(batch_id, task_id, *, manual_pick: bool):
    import time as _t
    from core.runtime_state import download_batches, download_tasks, tasks_lock

    with tasks_lock:
        download_tasks[task_id] = {
            'status': 'downloading',
            'username': 'soundcloud',
            'filename': '1234||https://sc/x||Display Name',
            'download_id': 'dl-1',
            '_user_manual_pick': manual_pick,
            'track_info': {'name': 'Test Track'},
            'track_index': 0,
            # Recent so the safety-valve "stuck-too-long" branch doesn't fire.
            'status_change_time': _t.time(),
            'cached_candidates': [],
        }
        download_batches[batch_id] = {
            'phase': 'downloading',
            'queue': [task_id],
            'analysis_results': [],
            'active_count': 1,
            'max_concurrent': 3,
        }


def _clear_runtime():
    from core.runtime_state import download_batches, download_tasks, tasks_lock
    with tasks_lock:
        download_tasks.clear()
        download_batches.clear()


@pytest.fixture
def runtime():
    """Seeded download_batches + download_tasks; cleared after each test."""
    _clear_runtime()
    yield
    _clear_runtime()


def _build_batch_deps(completed_calls):
    """StatusDeps with a long timeout so the safety valve doesn't fire +
    a captured ``on_download_completed`` we can assert against."""
    fake_config = MagicMock()
    fake_config.get = lambda key, default=None: 99999 if key == 'soulseek.download_timeout' else default

    return status_mod.StatusDeps(
        config_manager=fake_config,
        docker_resolve_path=lambda p: p,
        find_completed_file=lambda *a, **k: (None, None),
        make_context_key=lambda u, f: f"{u}::{f}",
        submit_post_processing=lambda *a: None,
        get_cached_transfer_data=lambda: {},
        download_orchestrator=None,  # IF branch doesn't need engine
        run_async=None,
        on_download_completed=lambda batch_id, task_id, success: completed_calls.append(
            (batch_id, task_id, success)
        ),
    )


def test_if_branch_manual_pick_marks_failed_on_errored(runtime):
    """Manual-pick task whose live_transfers entry reports Errored —
    must transition to 'failed' synchronously, not defer to the monitor."""
    import time as _t
    from core.runtime_state import download_batches

    batch_id = 'b1'
    task_id = 't1'
    _seed_runtime(batch_id, task_id, manual_pick=True)

    completed_calls = []
    deps = _build_batch_deps(completed_calls)

    live_transfers_lookup = {
        'soundcloud::1234||https://sc/x||Display Name': {
            'state': 'Errored',
            'percentComplete': 0,
            'errorMessage': 'HTTP 404 Not Found',
        }
    }

    response = status_mod.build_batch_status_data(
        batch_id, download_batches[batch_id], live_transfers_lookup, deps,
    )

    task_status = response['tasks'][0]
    assert task_status['status'] == 'failed'
    assert 'HTTP 404' in (task_status.get('error_message') or '')

    from core.runtime_state import download_tasks
    assert download_tasks[task_id]['status'] == 'failed'

    # on_download_completed is deferred to a daemon thread — wait briefly.
    for _ in range(50):
        if completed_calls:
            break
        _t.sleep(0.01)
    assert completed_calls == [(batch_id, task_id, False)]


def test_if_branch_compound_completed_errored_hits_manual_pick_failure(runtime):
    """``"Completed, Errored"`` must trigger the failure branch, not the
    success branch. Slskd / engine pluginscan emit compound states when
    a download technically completes but the file is corrupt / partial."""
    from core.runtime_state import download_batches

    batch_id = 'b1'
    task_id = 't1'
    _seed_runtime(batch_id, task_id, manual_pick=True)

    deps = _build_batch_deps([])
    live_transfers_lookup = {
        'soundcloud::1234||https://sc/x||Display Name': {
            'state': 'Completed, Errored',
            'percentComplete': 100,
        }
    }

    response = status_mod.build_batch_status_data(
        batch_id, download_batches[batch_id], live_transfers_lookup, deps,
    )

    assert response['tasks'][0]['status'] == 'failed'


def test_if_branch_auto_attempt_defers_to_monitor(runtime):
    """Auto attempts (no manual-pick flag) keep the original "let monitor
    handle retry" behavior — task stays in its current pre-error status
    so the monitor's retry path can detect the Errored live_info on its
    next tick. This is the byte-identical pre-fix behavior; the guard is
    additive."""
    from core.runtime_state import download_batches, download_tasks

    batch_id = 'b1'
    task_id = 't1'
    _seed_runtime(batch_id, task_id, manual_pick=False)

    deps = _build_batch_deps([])
    live_transfers_lookup = {
        'soundcloud::1234||https://sc/x||Display Name': {
            'state': 'Errored',
            'percentComplete': 0,
        }
    }

    status_mod.build_batch_status_data(
        batch_id, download_batches[batch_id], live_transfers_lookup, deps,
    )

    # Auto retry path keeps the task in 'downloading' so the monitor can
    # observe the Errored state on its own poll. NOT marked failed here.
    assert download_tasks[task_id]['status'] == 'downloading'


def test_orchestrator_exception_swallowed():
    """If get_download_status raises, the fallback logs + bails — it must
    not propagate and crash the whole status response."""
    task = _task()
    task_status = {'status': 'downloading', 'progress': 0}

    fake_orch = MagicMock()

    async def _boom(_):
        raise RuntimeError("network blip")

    fake_orch.get_download_status = _boom
    deps = status_mod.StatusDeps(
        config_manager=MagicMock(),
        docker_resolve_path=lambda p: p,
        find_completed_file=lambda *a, **k: (None, None),
        make_context_key=lambda u, f: f"{u}::{f}",
        submit_post_processing=lambda *a: None,
        get_cached_transfer_data=lambda: {},
        download_orchestrator=fake_orch,
        run_async=lambda coro: __import__('asyncio').new_event_loop().run_until_complete(coro),
    )

    status_mod._apply_engine_state_fallback('t1', task, task_status, 'b1', deps)

    assert task['status'] == 'downloading'
