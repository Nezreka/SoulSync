"""Engine-boundary tests for the automation handler registration.

Per-handler boundary tests in the sibling test files prove each
handler's body works in isolation. These tests prove the
**registration layer** wires every handler to the right action name,
attaches the right guard, and registers the four progress callbacks
in the slots the engine expects.

The kettui standard for refactor PRs: don't ship a "behavior
preserved" claim that's only validated at the function boundary.
Wire the seam — engine + register_all + deps — and exercise it.

These tests use a minimal recording engine that captures every
``register_action_handler`` / ``register_progress_callbacks`` call,
plus a no-op ``add_scan_completion_callback`` on a fake scan
manager. No real AutomationEngine, no real DB, no real Flask app.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Tuple

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers import register_all


# Every action name `register_all` is expected to register. Drift
# (rename / new handler / removed handler) fails this test
# immediately so refactor PRs can't quietly drop a handler.
EXPECTED_ACTION_NAMES = frozenset({
    'process_wishlist',
    'scan_watchlist',
    'scan_library',
    'refresh_mirrored',
    'sync_playlist',
    'discover_playlist',
    'playlist_pipeline',
    'start_database_update',
    'deep_scan_library',
    'run_duplicate_cleaner',
    'clear_quarantine',
    'cleanup_wishlist',
    'update_discovery_pool',
    'start_quality_scan',
    'backup_database',
    'refresh_beatport_cache',
    'clean_search_history',
    'clean_completed_downloads',
    'full_cleanup',
    'run_script',
    'search_and_download',
})

# Action names that MUST register a guard (duplicate-run prevention).
EXPECTED_GUARDED_ACTIONS = frozenset({
    'process_wishlist',
    'scan_watchlist',
    'scan_library',
    'playlist_pipeline',
    'start_database_update',
    'deep_scan_library',
    'run_duplicate_cleaner',
    'start_quality_scan',
})


class _RecordingEngine:
    """Minimal AutomationEngine stand-in. Captures everything
    register_all does so tests can assert on it."""

    def __init__(self):
        self.action_handlers: Dict[str, Dict[str, Any]] = {}
        self.progress_callbacks: Tuple = ()
        self.emits: List[Tuple[str, dict]] = []

    def register_action_handler(self, action_type, handler_fn, guard_fn=None):
        self.action_handlers[action_type] = {'handler': handler_fn, 'guard': guard_fn}

    def register_progress_callbacks(self, init_fn, finish_fn, update_fn=None, history_fn=None):
        self.progress_callbacks = (init_fn, finish_fn, update_fn, history_fn)

    def emit(self, event_name, payload):
        self.emits.append((event_name, payload))


class _RecordingScanMgr:
    _current_server_type = 'plex'

    def __init__(self):
        self.callbacks: List = []

    def add_scan_completion_callback(self, cb):
        self.callbacks.append(cb)


def _build_deps(engine, scan_mgr=None) -> AutomationDeps:
    class _Logger:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    class _Cfg:
        def get(self, key, default=None): return default
        def get_active_media_server(self): return 'plex'

    return AutomationDeps(
        engine=engine,
        state=AutomationState(),
        config_manager=_Cfg(),
        update_progress=lambda *a, **k: None,
        logger=_Logger(),
        get_database=lambda: object(),
        spotify_client=None,
        tidal_client=None,
        web_scan_manager=scan_mgr,
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


# ─── action handler registration ─────────────────────────────────────


class TestActionHandlerRegistration:
    def test_every_expected_action_name_registered(self):
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        registered = set(engine.action_handlers.keys())
        missing = EXPECTED_ACTION_NAMES - registered
        extra = registered - EXPECTED_ACTION_NAMES
        assert not missing, f"register_all dropped: {missing}"
        assert not extra, f"register_all added unexpected: {extra}"

    def test_guarded_actions_have_a_guard(self):
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        for name in EXPECTED_GUARDED_ACTIONS:
            assert engine.action_handlers[name]['guard'] is not None, (
                f"action {name!r} expected to register a guard but didn't"
            )

    def test_unguarded_actions_have_no_guard(self):
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        unguarded = EXPECTED_ACTION_NAMES - EXPECTED_GUARDED_ACTIONS
        for name in unguarded:
            assert engine.action_handlers[name]['guard'] is None, (
                f"action {name!r} unexpectedly registered a guard"
            )

    def test_every_handler_callable(self):
        # Every registered handler must be callable with a config dict.
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        for name, entry in engine.action_handlers.items():
            handler = entry['handler']
            assert callable(handler), f"{name} handler is not callable"

    def test_every_guard_callable_returns_bool(self):
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        for name, entry in engine.action_handlers.items():
            guard = entry['guard']
            if guard is None:
                continue
            value = guard()
            assert isinstance(value, bool), (
                f"{name} guard returned non-bool: {type(value).__name__}"
            )


# ─── progress callback registration ──────────────────────────────────


class TestProgressCallbackRegistration:
    def test_all_four_callbacks_registered(self):
        engine = _RecordingEngine()
        register_all(_build_deps(engine))
        init_fn, finish_fn, update_fn, history_fn = engine.progress_callbacks
        assert callable(init_fn)
        assert callable(finish_fn)
        assert callable(update_fn)
        assert callable(history_fn)

    def test_progress_init_callback_invocable(self):
        # Engine signature: init_fn(aid, name, action_type)
        engine = _RecordingEngine()
        captured: List[Tuple] = []
        deps = _build_deps(engine)
        deps = AutomationDeps(**{
            **{f.name: getattr(deps, f.name) for f in deps.__dataclass_fields__.values()},
            'init_automation_progress': lambda aid, name, at: captured.append((aid, name, at)),
        })
        register_all(deps)
        init_fn, _, _, _ = engine.progress_callbacks
        init_fn('auto-1', 'My Auto', 'wishlist')
        assert captured == [('auto-1', 'My Auto', 'wishlist')]

    def test_progress_finish_callback_invocable(self):
        # Engine signature: finish_fn(aid, result)
        engine = _RecordingEngine()
        captured: List[Tuple] = []
        deps = _build_deps(engine)
        deps = AutomationDeps(**{
            **{f.name: getattr(deps, f.name) for f in deps.__dataclass_fields__.values()},
            'update_progress': lambda aid, **kw: captured.append((aid, kw)),
        })
        register_all(deps)
        _, finish_fn, _, _ = engine.progress_callbacks
        # Non-_manages_own_progress result triggers update_progress emit.
        finish_fn('auto-1', {'status': 'completed'})
        assert len(captured) == 1
        assert captured[0][0] == 'auto-1'
        assert captured[0][1]['status'] == 'finished'

    def test_progress_finish_skips_self_managed(self):
        engine = _RecordingEngine()
        captured: List[Tuple] = []
        deps = _build_deps(engine)
        deps = AutomationDeps(**{
            **{f.name: getattr(deps, f.name) for f in deps.__dataclass_fields__.values()},
            'update_progress': lambda aid, **kw: captured.append((aid, kw)),
        })
        register_all(deps)
        _, finish_fn, _, _ = engine.progress_callbacks
        finish_fn('auto-1', {'_manages_own_progress': True, 'status': 'completed'})
        assert captured == []

    def test_history_callback_invocable_with_db(self):
        # Engine signature: history_fn(aid, result)
        engine = _RecordingEngine()
        captured: List[Tuple] = []
        db_obj = object()
        deps = _build_deps(engine)
        deps = AutomationDeps(**{
            **{f.name: getattr(deps, f.name) for f in deps.__dataclass_fields__.values()},
            'get_database': lambda: db_obj,
            'record_progress_history': lambda aid, result, db: captured.append((aid, result, db)),
        })
        register_all(deps)
        _, _, _, history_fn = engine.progress_callbacks
        history_fn('auto-1', {'status': 'completed'})
        assert captured == [('auto-1', {'status': 'completed'}, db_obj)]


# ─── library_scan_completed wiring ───────────────────────────────────


class TestLibraryScanCompletedEmitter:
    def test_no_scan_manager_safe(self):
        # Should not raise when scan manager is absent (test/headless mode).
        engine = _RecordingEngine()
        register_all(_build_deps(engine, scan_mgr=None))
        # No callbacks captured (no scan manager to register against),
        # but engine still has all the handlers.
        assert engine.action_handlers

    def test_scan_completion_callback_registered(self):
        engine = _RecordingEngine()
        scan_mgr = _RecordingScanMgr()
        register_all(_build_deps(engine, scan_mgr=scan_mgr))
        assert len(scan_mgr.callbacks) == 1
        # Invoking the callback should fire engine.emit('library_scan_completed', ...).
        scan_mgr.callbacks[0]()
        assert engine.emits == [('library_scan_completed', {'server_type': 'plex'})]


# ─── handler invocation through the engine boundary ─────────────────


class TestHandlerInvocation:
    """For each registered handler, exercise the lambda the engine
    would call. Verifies the deps closure is captured correctly + the
    handler returns a result dict.

    Forces every long-running handler down its guard short-circuit
    path by pre-setting the relevant `*_state` dicts to ``running``
    (database_update, deep_scan, duplicate_cleaner, quality_scanner)
    or by pre-occupying the state flags (scan_library, playlist_pipeline).
    Other handlers either return error early (no playlist specified,
    no scan manager, etc) or run cleanly against the no-op stub deps.
    """

    def test_every_handler_returns_dict(self):
        engine = _RecordingEngine()
        # Pre-set state dicts so guarded handlers skip-return cleanly.
        running_state = {'status': 'running'}
        active_batches = {'b1': {'phase': 'downloading'}}

        # Stub DB that satisfies the handlers reaching for it on
        # short paths. cleanup_wishlist calls remove_wishlist_duplicates.
        # update_discovery_pool's exception path swallows missing
        # scanner. Other handlers either short-circuit or use stub
        # callables.
        class _StubDB:
            def remove_wishlist_duplicates(self, profile_id): return 0
            def get_mirrored_playlists(self): return []
            def get_mirrored_playlist(self, _id): return None
            def get_mirrored_playlist_tracks(self, _id): return []

        # Stub Flask app so refresh_beatport_cache's test_client call
        # doesn't crash. Use a minimal context manager.
        class _FakeResponse:
            status_code = 200
        class _FakeClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, _path): return _FakeResponse()
        class _StubApp:
            def test_client(self): return _FakeClient()

        deps = _build_deps(engine)
        deps = AutomationDeps(**{
            **{f.name: getattr(deps, f.name) for f in deps.__dataclass_fields__.values()},
            'get_db_update_state': lambda: running_state,
            'get_duplicate_cleaner_state': lambda: running_state,
            'get_quality_scanner_state': lambda: running_state,
            'get_download_batches': lambda: active_batches,  # forces clean_completed_downloads to skip
            'get_database': lambda: _StubDB(),
            'get_app': lambda: _StubApp(),
        })
        # Pre-set state flags too so scan_library + playlist_pipeline guards fire.
        deps.state.scan_library_automation_id = 'someone-else'
        deps.state.pipeline_running = True

        # Patch time.sleep across handler modules so refresh_beatport_cache
        # (which sleeps 2s between sections) doesn't extend the test.
        import core.automation.handlers.maintenance as maint_mod
        original_sleep = maint_mod.time.sleep
        maint_mod.time.sleep = lambda _: None

        register_all(deps)

        try:
            for name, entry in engine.action_handlers.items():
                handler = entry['handler']
                # Minimal config: trigger the natural error/skip paths
                # for handlers that need a playlist_id, query, script_name etc.
                result = handler({'_automation_id': 'test', 'all': False})
                assert isinstance(result, dict), (
                    f"handler {name!r} returned {type(result).__name__}, expected dict"
                )
                assert 'status' in result, (
                    f"handler {name!r} returned dict without 'status': {list(result.keys())}"
                )
        finally:
            maint_mod.time.sleep = original_sleep
