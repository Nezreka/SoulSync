"""Boundary tests for the progress + history callbacks extracted
from ``web_server._register_automation_handlers``.

The callbacks are wired by the engine via ``register_progress_callbacks``;
each test invokes the extracted top-level function with stub deps
and verifies the right downstream call fires."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Tuple

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.progress_callbacks import (
    progress_init,
    progress_finish,
    record_history,
    on_library_scan_completed,
    register_library_scan_completed_emitter,
)


def _build_deps(**overrides) -> AutomationDeps:
    class _StubLogger:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

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
        init_automation_progress=lambda *a, **k: None,
        record_progress_history=lambda *a, **k: None,
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


# ─── progress_init ───────────────────────────────────────────────────


class TestProgressInit:
    def test_forwards_to_init_automation_progress(self):
        captured: List[Tuple] = []

        def fake(aid, name, action_type):
            captured.append((aid, name, action_type))

        deps = _build_deps(init_automation_progress=fake)
        progress_init('auto-1', 'My Auto', 'wishlist', deps)
        assert captured == [('auto-1', 'My Auto', 'wishlist')]


# ─── progress_finish ─────────────────────────────────────────────────


class TestProgressFinish:
    def test_skips_when_handler_manages_own_progress(self):
        # Handler set the flag — engine callback must NOT emit a
        # second 'finished' over the top of the handler's own.
        calls: List[Dict] = []
        deps = _build_deps(update_progress=lambda *a, **k: calls.append({'a': a, 'k': k}))
        progress_finish('auto-1', {'_manages_own_progress': True, 'status': 'completed'}, deps)
        assert calls == []

    def test_completed_emits_finished_status(self):
        calls: List[Dict] = []
        deps = _build_deps(update_progress=lambda aid, **kw: calls.append({'aid': aid, **kw}))
        progress_finish('auto-1', {'status': 'completed'}, deps)
        assert len(calls) == 1
        assert calls[0]['aid'] == 'auto-1'
        assert calls[0]['status'] == 'finished'
        assert calls[0]['progress'] == 100
        assert calls[0]['phase'] == 'Complete'
        assert calls[0]['log_type'] == 'success'

    def test_error_status_emits_error_phase(self):
        calls: List[Dict] = []
        deps = _build_deps(update_progress=lambda aid, **kw: calls.append({'aid': aid, **kw}))
        progress_finish('auto-1', {'status': 'error', 'error': 'boom'}, deps)
        assert calls[0]['status'] == 'error'
        assert calls[0]['phase'] == 'Error'
        assert calls[0]['log_line'] == 'boom'
        assert calls[0]['log_type'] == 'error'

    def test_msg_falls_back_through_keys(self):
        # error -> reason -> status -> 'done'
        calls: List[Dict] = []
        deps = _build_deps(update_progress=lambda aid, **kw: calls.append({'aid': aid, **kw}))
        progress_finish('auto-1', {'status': 'completed', 'reason': 'all good'}, deps)
        assert calls[0]['log_line'] == 'all good'

    def test_msg_default_done(self):
        calls: List[Dict] = []
        deps = _build_deps(update_progress=lambda aid, **kw: calls.append({'aid': aid, **kw}))
        progress_finish('auto-1', {}, deps)
        assert calls[0]['log_line'] == 'done'


# ─── record_history ──────────────────────────────────────────────────


class TestRecordHistory:
    def test_passes_db_to_recorder(self):
        captured: List[Tuple] = []
        db_obj = object()
        deps = _build_deps(
            get_database=lambda: db_obj,
            record_progress_history=lambda aid, result, db: captured.append((aid, result, db)),
        )
        record_history('auto-1', {'status': 'completed'}, deps)
        assert captured == [('auto-1', {'status': 'completed'}, db_obj)]


# ─── on_library_scan_completed ───────────────────────────────────────


class TestOnLibraryScanCompleted:
    def test_no_engine_skips(self):
        deps = _build_deps(engine=None)
        # Should not raise.
        on_library_scan_completed(deps)

    def test_emits_event_with_server_type(self):
        emits: List[Tuple] = []

        class _Engine:
            def emit(self, name, payload):
                emits.append((name, payload))

        class _ScanMgr:
            _current_server_type = 'plex'

        deps = _build_deps(engine=_Engine(), web_scan_manager=_ScanMgr())
        on_library_scan_completed(deps)
        assert emits == [('library_scan_completed', {'server_type': 'plex'})]

    def test_unknown_server_type_when_attr_missing(self):
        emits: List[Tuple] = []

        class _Engine:
            def emit(self, name, payload):
                emits.append((name, payload))

        deps = _build_deps(engine=_Engine(), web_scan_manager=object())
        on_library_scan_completed(deps)
        assert emits[0][1] == {'server_type': 'unknown'}


# ─── register_library_scan_completed_emitter ─────────────────────────


class TestRegisterEmitter:
    def test_no_scan_manager_noop(self):
        # No web_scan_manager → no callback registered, no error.
        deps = _build_deps(web_scan_manager=None)
        register_library_scan_completed_emitter(deps)

    def test_registers_callback_with_scan_manager(self):
        callbacks: List = []

        class _ScanMgr:
            _current_server_type = 'plex'
            def add_scan_completion_callback(self, cb):
                callbacks.append(cb)

        deps = _build_deps(web_scan_manager=_ScanMgr())
        register_library_scan_completed_emitter(deps)
        assert len(callbacks) == 1
        # The registered callback must invoke without args (web_scan_manager
        # calls completion callbacks with no params).
        # Verify it does fire on_library_scan_completed when invoked.
        emits: List = []

        class _Engine:
            def emit(self, name, payload):
                emits.append((name, payload))

        deps2 = _build_deps(engine=_Engine(), web_scan_manager=_ScanMgr())
        register_library_scan_completed_emitter(deps2)
        # The lambda captured deps2; we need to grab the registered
        # callback to invoke it. Re-register and capture.
        captured = []
        class _Mgr2:
            _current_server_type = 'jellyfin'
            def add_scan_completion_callback(self, cb):
                captured.append(cb)
        deps3 = _build_deps(engine=_Engine(), web_scan_manager=_Mgr2())
        emits3 = []
        deps3 = _build_deps(
            engine=type('E', (), {'emit': lambda self, n, p: emits3.append((n, p))})(),
            web_scan_manager=_Mgr2(),
        )
        register_library_scan_completed_emitter(deps3)
        captured[0]()  # invoke the registered callback
        assert emits3 == [('library_scan_completed', {'server_type': 'jellyfin'})]
