"""Boundary tests for the maintenance + misc automation handlers
(database_update / deep_scan_library / duplicate_cleaner /
quality_scanner / clear_quarantine / cleanup_wishlist /
update_discovery_pool / backup_database / refresh_beatport_cache /
clean_search_history / clean_completed_downloads / full_cleanup /
run_script / search_and_download).

Each handler is tested as a pure function via stub deps. The bodies
are mechanical lifts of the closures that used to live in
``web_server._register_automation_handlers`` — these tests pin the
seam (deps wiring, exception swallow contract, return shapes,
guard short-circuits) so future drift fails here, not at runtime
against real executors / clients."""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.database_update import (
    auto_start_database_update, auto_deep_scan_library,
)
from core.automation.handlers.duplicate_cleaner import auto_run_duplicate_cleaner
from core.automation.handlers.quality_scanner import auto_start_quality_scan
from core.automation.handlers.maintenance import (
    auto_clear_quarantine, auto_cleanup_wishlist,
    auto_update_discovery_pool, auto_backup_database,
)
from core.automation.handlers.download_cleanup import (
    auto_clean_search_history, auto_clean_completed_downloads,
)
from core.automation.handlers.run_script import auto_run_script
from core.automation.handlers.search_and_download import auto_search_and_download


class _StubLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _StubConfig:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return 'plex'


def _build_deps(**overrides) -> AutomationDeps:
    defaults = dict(
        engine=object(),
        state=AutomationState(),
        config_manager=_StubConfig(),
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
        build_personalized_manager=lambda: None,
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


# ─── database_update / deep_scan ──────────────────────────────────────


class _StubExecutor:
    def __init__(self):
        self.submits: List[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.submits.append((fn, args, kwargs))


class TestDatabaseUpdate:
    def test_already_running_returns_skipped(self):
        state = {'status': 'running'}
        deps = _build_deps(get_db_update_state=lambda: state)
        result = auto_start_database_update({}, deps)
        assert result == {'status': 'skipped', 'reason': 'Database update already running'}

    def test_set_db_update_automation_id_called(self):
        # Handler must propagate the automation id through the deps
        # setter so the legacy global stays in sync.
        captured: List[Any] = []
        state = {'status': 'idle'}
        # Make the polling loop terminate immediately by flipping
        # the status as soon as it's set to 'running'.
        executor = _StubExecutor()

        def fake_task(*_a, **_k):
            state['status'] = 'finished'

        executor.submit = lambda fn, *a, **k: fake_task()
        deps = _build_deps(
            get_db_update_state=lambda: state,
            db_update_executor=executor,
            set_db_update_automation_id=lambda v: captured.append(v),
            run_db_update_task=fake_task,
        )
        # Replace time.sleep so we don't actually wait
        import core.automation.handlers.database_update as module
        original = module.time.sleep
        module.time.sleep = lambda _: None
        try:
            result = auto_start_database_update({'_automation_id': 'auto-1'}, deps)
        finally:
            module.time.sleep = original
        assert captured == ['auto-1']
        assert result['status'] == 'completed'


class TestDeepScan:
    def test_already_running_returns_skipped(self):
        state = {'status': 'running'}
        deps = _build_deps(get_db_update_state=lambda: state)
        result = auto_deep_scan_library({}, deps)
        assert result == {'status': 'skipped', 'reason': 'Database update already running'}


# ─── duplicate_cleaner ────────────────────────────────────────────────


class TestDuplicateCleaner:
    def test_already_running_returns_skipped(self):
        state = {'status': 'running'}
        deps = _build_deps(get_duplicate_cleaner_state=lambda: state)
        result = auto_run_duplicate_cleaner({}, deps)
        assert result == {'status': 'skipped', 'reason': 'Duplicate cleaner already running'}


# ─── quality_scanner ──────────────────────────────────────────────────


class TestQualityScanner:
    def test_already_running_returns_skipped(self):
        state = {'status': 'running'}
        deps = _build_deps(get_quality_scanner_state=lambda: state)
        result = auto_start_quality_scan({}, deps)
        assert result == {'status': 'skipped', 'reason': 'Quality scan already running'}


# ─── clear_quarantine ────────────────────────────────────────────────


class TestClearQuarantine:
    def test_no_quarantine_folder_returns_zero(self, tmp_path):
        # Point at a non-existent path.
        deps = _build_deps(
            config_manager=_StubConfig({'soulseek.download_path': str(tmp_path / 'nonexistent')}),
            docker_resolve_path=lambda p: p,
        )
        result = auto_clear_quarantine({}, deps)
        assert result == {'status': 'completed', 'removed': '0'}

    def test_clears_files_from_quarantine(self, tmp_path):
        download_path = tmp_path
        quarantine_path = download_path / 'ss_quarantine'
        quarantine_path.mkdir()
        (quarantine_path / 'a.flac').write_bytes(b'')
        (quarantine_path / 'b.flac').write_bytes(b'')
        deps = _build_deps(
            config_manager=_StubConfig({'soulseek.download_path': str(download_path)}),
            docker_resolve_path=lambda p: p,
        )
        result = auto_clear_quarantine({}, deps)
        assert result == {'status': 'completed', 'removed': '2'}


# ─── cleanup_wishlist ────────────────────────────────────────────────


class TestCleanupWishlist:
    def test_returns_count_from_db(self):
        class _DB:
            def remove_wishlist_duplicates(self, profile_id):
                assert profile_id == 1
                return 7

        deps = _build_deps(get_database=lambda: _DB())
        result = auto_cleanup_wishlist({}, deps)
        assert result == {'status': 'completed', 'removed': '7'}

    def test_returns_zero_when_db_returns_falsey(self):
        class _DB:
            def remove_wishlist_duplicates(self, profile_id):
                return None

        deps = _build_deps(get_database=lambda: _DB())
        result = auto_cleanup_wishlist({}, deps)
        assert result == {'status': 'completed', 'removed': '0'}


# ─── update_discovery_pool ───────────────────────────────────────────


class TestUpdateDiscoveryPool:
    def test_success(self):
        called: List[Any] = []

        class _Scanner:
            def update_discovery_pool_incremental(self, profile_id):
                called.append(profile_id)

        deps = _build_deps(
            get_watchlist_scanner=lambda spc: _Scanner(),
            get_current_profile_id=lambda: 7,
        )
        result = auto_update_discovery_pool({}, deps)
        assert result['status'] == 'completed'
        assert result['_manages_own_progress'] is True
        assert called == [7]

    def test_exception_swallowed(self):
        def boom(_):
            raise RuntimeError('scanner down')

        deps = _build_deps(get_watchlist_scanner=boom)
        result = auto_update_discovery_pool({}, deps)
        assert result['status'] == 'error'
        assert 'scanner down' in result['reason']


# ─── backup_database ─────────────────────────────────────────────────


class TestBackupDatabase:
    def test_missing_database_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'no.db'))
        deps = _build_deps()
        result = auto_backup_database({}, deps)
        assert result == {'status': 'error', 'reason': 'Database file not found'}


# ─── clean_search_history ────────────────────────────────────────────


class TestCleanSearchHistory:
    def test_soulseek_inactive_skips(self):
        deps = _build_deps(
            config_manager=_StubConfig({'download_source.mode': 'tidal'}),
        )
        result = auto_clean_search_history({}, deps)
        assert result == {'status': 'skipped'}

    def test_no_orchestrator_skips(self):
        deps = _build_deps(
            config_manager=_StubConfig({'download_source.mode': 'soulseek'}),
            download_orchestrator=None,
        )
        result = auto_clean_search_history({}, deps)
        assert result == {'status': 'skipped'}


# ─── clean_completed_downloads ───────────────────────────────────────


class TestCleanCompletedDownloads:
    def test_active_batches_skip(self):
        # Active batch present → handler returns 'completed' without doing anything.
        deps = _build_deps(
            get_download_batches=lambda: {'b1': {'phase': 'downloading'}},
            get_download_tasks=lambda: {},
        )
        result = auto_clean_completed_downloads({}, deps)
        assert result == {'status': 'completed'}


# ─── run_script ──────────────────────────────────────────────────────


class TestRunScript:
    def test_no_script_name_returns_error(self):
        deps = _build_deps()
        result = auto_run_script({}, deps)
        assert result == {'status': 'error', 'error': 'No script selected'}

    def test_path_traversal_blocked(self, tmp_path):
        scripts_dir = tmp_path / 'scripts'
        scripts_dir.mkdir()
        # Place a script OUTSIDE the scripts dir + try to reach it
        # via ../ traversal.
        evil = tmp_path / 'evil.sh'
        evil.write_text('#!/bin/bash\necho evil')
        deps = _build_deps(
            config_manager=_StubConfig({'scripts.path': str(scripts_dir)}),
            docker_resolve_path=lambda p: p,
        )
        result = auto_run_script({'script_name': '../evil.sh'}, deps)
        assert result['status'] == 'error'
        assert 'path traversal' in result['error'].lower()

    def test_missing_script_returns_error(self, tmp_path):
        scripts_dir = tmp_path / 'scripts'
        scripts_dir.mkdir()
        deps = _build_deps(
            config_manager=_StubConfig({'scripts.path': str(scripts_dir)}),
        )
        result = auto_run_script({'script_name': 'no_such_script.sh'}, deps)
        assert result['status'] == 'error'
        assert 'not found' in result['error'].lower()


# ─── search_and_download ─────────────────────────────────────────────


class TestSearchAndDownload:
    def test_no_query_returns_error(self):
        deps = _build_deps()
        result = auto_search_and_download({}, deps)
        assert result == {'status': 'error', 'error': 'No search query provided'}

    def test_query_from_event_data_used(self):
        captured_queries: List[str] = []

        class _Orchestrator:
            async def search_and_download_best(self, q):
                captured_queries.append(q)
                return 'dl-id-123'

        deps = _build_deps(
            download_orchestrator=_Orchestrator(),
            run_async=lambda coro: 'dl-id-123',
        )
        result = auto_search_and_download(
            {'_event_data': {'query': 'Adele Hello'}}, deps,
        )
        assert result['status'] == 'completed'
        assert result['query'] == 'Adele Hello'
        assert result['download_id'] == 'dl-id-123'

    def test_no_match_returns_not_found(self):
        class _Orchestrator:
            async def search_and_download_best(self, q):
                return None

        deps = _build_deps(
            download_orchestrator=_Orchestrator(),
            run_async=lambda coro: None,
        )
        result = auto_search_and_download({'query': 'xyz'}, deps)
        assert result['status'] == 'not_found'
        assert result['query'] == 'xyz'
