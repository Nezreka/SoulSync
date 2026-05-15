"""Boundary tests for the simple extracted automation handlers
(``process_wishlist``, ``scan_watchlist``, ``scan_library``).

Each handler is tested as a pure function: real ``AutomationDeps``
constructed with stub callables, no Flask, no DB, no media-server
clients. The tests exercise the success path, the guard paths
(handler short-circuits when another instance is running), the
exception-swallowing contract (handlers must NEVER raise into the
engine), and the mutable-state machinery for handlers that own a
flag in ``AutomationState``.

Pre-extraction these closures lived inside
``web_server._register_automation_handlers`` and were essentially
un-testable — every test would have needed to spin up the whole
Flask app and stub a dozen module-level globals."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.process_wishlist import auto_process_wishlist
from core.automation.handlers.scan_watchlist import auto_scan_watchlist
from core.automation.handlers.scan_library import auto_scan_library


# ─── shared test scaffolding ──────────────────────────────────────────


def _build_deps(**overrides: Any) -> AutomationDeps:
    """Return a default `AutomationDeps` with no-op callables. Tests
    pass ``overrides`` to install behaviour on the specific deps they
    care about."""

    class _StubLogger:
        def debug(self, *_a, **_k): pass
        def info(self, *_a, **_k): pass
        def warning(self, *_a, **_k): pass
        def error(self, *_a, **_k): pass

    defaults = dict(
        engine=object(),
        state=AutomationState(),
        config_manager=object(),
        update_progress=lambda *a, **k: None,
        logger=_StubLogger(),
        get_database=lambda: object(),
        spotify_client=None,
        tidal_client=None,
        web_scan_manager=None,
        process_wishlist_automatically=lambda **k: None,
        process_watchlist_scan_automatically=lambda **k: None,
        is_wishlist_actually_processing=lambda: False,
        is_watchlist_actually_scanning=lambda: False,
        get_watchlist_scan_state=lambda: {},
        run_playlist_discovery_worker=lambda *a, **k: None,
        run_sync_task=lambda *a, **k: None,
        load_sync_status_file=lambda: {},
        get_deezer_client=lambda: None,
        parse_youtube_playlist=lambda url: None,
        get_sync_states=lambda: {},
        set_db_update_automation_id=lambda v: None,
        get_db_update_state=lambda: {},
        db_update_lock=threading.Lock(),
        db_update_executor=None,
        run_db_update_task=lambda *a, **k: None,
        run_deep_scan_task=lambda *a, **k: None,
        get_duplicate_cleaner_state=lambda: {},
        duplicate_cleaner_lock=threading.Lock(),
        duplicate_cleaner_executor=None,
        run_duplicate_cleaner=lambda: None,
        get_quality_scanner_state=lambda: {},
        quality_scanner_lock=threading.Lock(),
        quality_scanner_executor=None,
        run_quality_scanner=lambda *a, **k: None,
        download_orchestrator=None,
        run_async=lambda coro: None,
        tasks_lock=threading.Lock(),
        get_download_batches=lambda: {},
        get_download_tasks=lambda: {},
        sweep_empty_download_directories=lambda: 0,
        get_staging_path=lambda: '/staging',
        docker_resolve_path=lambda p: p,
        get_current_profile_id=lambda: 1,
        get_watchlist_scanner=lambda spc: None,
        get_app=lambda: None,
        get_beatport_data_cache=lambda: {'cache_lock': threading.Lock(), 'homepage': {}},
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


# ─── process_wishlist ─────────────────────────────────────────────────


class TestProcessWishlist:
    def test_success_returns_completed_status(self):
        called: List[Any] = []

        def stub(automation_id=None):
            called.append(automation_id)

        deps = _build_deps(process_wishlist_automatically=stub)
        result = auto_process_wishlist({'_automation_id': 'auto-1'}, deps)
        assert result == {'status': 'completed'}
        assert called == ['auto-1']

    def test_passes_none_when_no_automation_id(self):
        called: List[Any] = []

        def stub(automation_id=None):
            called.append(automation_id)

        deps = _build_deps(process_wishlist_automatically=stub)
        result = auto_process_wishlist({}, deps)
        assert result == {'status': 'completed'}
        assert called == [None]

    def test_handler_swallows_exceptions(self):
        def stub(**_kwargs):
            raise RuntimeError('boom')

        deps = _build_deps(process_wishlist_automatically=stub)
        result = auto_process_wishlist({'_automation_id': 'a'}, deps)
        assert result == {'status': 'error', 'error': 'boom'}


# ─── scan_watchlist ──────────────────────────────────────────────────


class TestScanWatchlist:
    def test_fresh_scan_reports_summary_stats(self):
        # Worker reassigns the state dict mid-run — handler detects
        # via id() change and reports stats.
        states = [
            {'summary': {}},
            {'summary': {
                'total_artists': 5,
                'successful_scans': 4,
                'new_tracks_found': 12,
                'tracks_added_to_wishlist': 8,
            }},
        ]
        idx = {'i': 0}

        def get_state():
            return states[idx['i']]

        def stub(**_kwargs):
            idx['i'] = 1  # simulate the worker swapping the dict

        deps = _build_deps(
            process_watchlist_scan_automatically=stub,
            get_watchlist_scan_state=get_state,
        )
        result = auto_scan_watchlist({}, deps)
        assert result == {
            'status': 'completed',
            'artists_scanned': 5,
            'successful_scans': 4,
            'new_tracks_found': 12,
            'tracks_added_to_wishlist': 8,
        }

    def test_no_fresh_scan_returns_bare_completed(self):
        # Same dict identity before and after = no fresh scan ran.
        same_dict = {'summary': {'total_artists': 999}}
        deps = _build_deps(
            process_watchlist_scan_automatically=lambda **_k: None,
            get_watchlist_scan_state=lambda: same_dict,
        )
        result = auto_scan_watchlist({}, deps)
        assert result == {'status': 'completed'}

    def test_handler_swallows_exceptions(self):
        def stub(**_kwargs):
            raise ValueError('no scanner')

        deps = _build_deps(process_watchlist_scan_automatically=stub)
        result = auto_scan_watchlist({}, deps)
        assert result == {'status': 'error', 'error': 'no scanner'}


# ─── scan_library ────────────────────────────────────────────────────


@dataclass
class _StubScanManager:
    """Minimal fake of ``web_scan_manager`` — records calls + lets
    tests script its responses."""

    request_responses: List[dict] = field(default_factory=list)
    status_responses: List[dict] = field(default_factory=list)
    request_calls: List[str] = field(default_factory=list)

    def request_scan(self, label: str) -> dict:
        self.request_calls.append(label)
        return self.request_responses.pop(0) if self.request_responses else {'status': 'queued'}

    def get_scan_status(self) -> dict:
        return self.status_responses.pop(0) if self.status_responses else {'status': 'idle'}


class TestScanLibrary:
    def test_no_scan_manager_returns_error(self):
        deps = _build_deps(web_scan_manager=None)
        result = auto_scan_library({'_automation_id': 'a'}, deps)
        assert result == {'status': 'error', 'reason': 'Scan manager not available'}

    def test_already_tracked_returns_skipped(self):
        # Pre-set the state flag — handler should short-circuit.
        state = AutomationState()
        state.scan_library_automation_id = 'someone-else'
        scanner = _StubScanManager(request_responses=[{'status': 'queued'}])
        deps = _build_deps(state=state, web_scan_manager=scanner)
        result = auto_scan_library({'_automation_id': 'a'}, deps)
        assert result == {'status': 'skipped', 'reason': 'Scan already being tracked'}
        assert scanner.request_calls == ['Automation trigger (additional batch)']

    def test_scan_completes_normally(self):
        # request_scan returns scheduled; first poll = scheduled;
        # second poll = scanning; third poll = idle.
        scanner = _StubScanManager(
            request_responses=[{'status': 'scheduled', 'delay_seconds': 5}],
            status_responses=[
                {'status': 'scheduled'},
                {'status': 'scanning', 'elapsed_seconds': 10, 'max_time_seconds': 100},
                {'status': 'idle'},
            ],
        )
        progress: List[dict] = []

        def stub_progress(automation_id, **kwargs):
            progress.append({'aid': automation_id, **kwargs})

        deps = _build_deps(
            web_scan_manager=scanner,
            update_progress=stub_progress,
        )
        # Patch time.sleep so the test runs instantly.
        import core.automation.handlers.scan_library as module
        original = module.time.sleep
        module.time.sleep = lambda _: None
        try:
            result = auto_scan_library({'_automation_id': 'auto-1'}, deps)
        finally:
            module.time.sleep = original

        assert result['status'] == 'completed'
        assert result.get('_manages_own_progress') is True
        # State flag cleaned up after run
        assert deps.state.scan_library_automation_id is None
        # Progress phases emitted: scheduled, scan-start, scanning, completed
        statuses = [p.get('status') for p in progress]
        assert 'finished' in statuses

    def test_state_cleanup_on_exception(self):
        class ExplodingScanner:
            def request_scan(self, _):
                raise RuntimeError('boom')

            def get_scan_status(self):
                return {'status': 'idle'}

        progress: List[dict] = []
        deps = _build_deps(
            web_scan_manager=ExplodingScanner(),
            update_progress=lambda aid, **kw: progress.append({'aid': aid, **kw}),
        )
        result = auto_scan_library({'_automation_id': 'auto-x'}, deps)
        assert result['status'] == 'error'
        assert result['_manages_own_progress'] is True
        # State flag still cleaned up
        assert deps.state.scan_library_automation_id is None
        # Error progress emitted
        assert any(p.get('status') == 'error' for p in progress)


# ─── AutomationState ──────────────────────────────────────────────────


class TestAutomationState:
    def test_default_state(self):
        s = AutomationState()
        assert s.scan_library_automation_id is None
        assert s.db_update_automation_id is None
        assert s.pipeline_running is False
        assert s.is_scan_library_active() is False
        assert s.is_pipeline_running() is False

    def test_set_scan_library_id(self):
        s = AutomationState()
        s.set_scan_library_id('auto-1')
        assert s.scan_library_automation_id == 'auto-1'
        assert s.is_scan_library_active() is True
        s.set_scan_library_id(None)
        assert s.is_scan_library_active() is False

    def test_set_pipeline_running(self):
        s = AutomationState()
        s.set_pipeline_running(True)
        assert s.is_pipeline_running() is True
        s.set_pipeline_running(False)
        assert s.is_pipeline_running() is False

    def test_concurrent_set_safe_via_lock(self):
        # Smoke test: two threads flipping the same field don't crash.
        # Lock ensures the final value is consistent.
        s = AutomationState()

        def worker():
            for _ in range(100):
                s.set_pipeline_running(True)
                s.set_pipeline_running(False)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert s.pipeline_running is False
