"""Tests for organize-by-playlist integration in the playlist pipeline tail."""

from unittest.mock import MagicMock

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
import threading


def _minimal_deps(**overrides):
    base = dict(
        engine=MagicMock(),
        state=AutomationState(),
        config_manager=MagicMock(),
        update_progress=lambda *a, **k: None,
        logger=MagicMock(),
        get_database=lambda: MagicMock(),
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
        run_playlist_organize_download=lambda **k: {'status': 'started', 'batch_id': 'b1'},
        missing_download_executor=None,
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
    base.update(overrides)
    return AutomationDeps(**base)  # type: ignore[arg-type]


def test_all_organize_playlists_skips_wishlist():
    wishlist_calls = []

    def sync_one(_pl):
        return {'status': 'skipped', 'reason': 'unchanged'}

    deps = _minimal_deps(
        process_wishlist_automatically=lambda **k: wishlist_calls.append(k),
    )
    playlists = [
        {'id': 1, 'name': 'A', 'organize_by_playlist': True},
    ]
    result = run_sync_and_wishlist(
        deps,
        'auto-1',
        playlists,
        sync_one_fn=sync_one,
        sync_id_for_fn=lambda pl: f'mirror_{pl["id"]}',
        skip_wishlist=False,
    )
    assert result['wishlist_queued'] == 0
    assert result['organize_downloads_started'] == 1
    assert wishlist_calls == []


def test_mixed_playlists_still_runs_wishlist():
    wishlist_calls = []

    deps = _minimal_deps(
        process_wishlist_automatically=lambda **k: wishlist_calls.append(1),
        is_wishlist_actually_processing=lambda: False,
    )
    playlists = [
        {'id': 1, 'name': 'Organized', 'organize_by_playlist': True},
        {'id': 2, 'name': 'Normal', 'organize_by_playlist': False},
    ]

    def sync_one(_pl):
        return {'status': 'skipped'}

    result = run_sync_and_wishlist(
        deps,
        None,
        playlists,
        sync_one_fn=sync_one,
        sync_id_for_fn=lambda pl: f'mirror_{pl["id"]}',
    )
    assert result['organize_downloads_started'] == 1
    assert result['wishlist_queued'] == 1
    assert len(wishlist_calls) == 1
