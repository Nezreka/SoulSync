"""Tests for the sync_downloaded_playlists automation handler and lifecycle integration."""

from unittest.mock import MagicMock, patch
import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.registration import register_all
from core.automation.handlers.sync_downloaded_playlists import auto_sync_downloaded_playlists
from core.automation_engine import SYSTEM_AUTOMATIONS, AutomationEngine
from core.automation.blocks import ACTIONS
from core.sync.post_download_sync import (
    register_completed_playlist_batch,
    clear_pending_playlist_syncs,
    get_pending_playlist_syncs_count,
)


@pytest.fixture(autouse=True)
def clean_pending():
    clear_pending_playlist_syncs()
    yield
    clear_pending_playlist_syncs()


def _make_deps(**overrides):
    state = AutomationState()
    defaults = {
        'engine': MagicMock(spec=AutomationEngine),
        'state': state,
        'config_manager': MagicMock(),
        'update_progress': MagicMock(),
        'logger': MagicMock(),
        'get_database': MagicMock(),
        'spotify_client': MagicMock(),
        'tidal_client': MagicMock(),
        'web_scan_manager': MagicMock(),
        'process_wishlist_automatically': MagicMock(),
        'process_watchlist_scan_automatically': MagicMock(),
        'is_wishlist_actually_processing': MagicMock(return_value=False),
        'is_watchlist_actually_scanning': MagicMock(return_value=False),
        'get_watchlist_scan_state': MagicMock(return_value={}),
        'run_playlist_discovery_worker': MagicMock(),
        'run_sync_task': MagicMock(),
        'run_playlist_organize_download': MagicMock(),
        'missing_download_executor': MagicMock(),
        'load_sync_status_file': MagicMock(return_value={}),
        'get_deezer_client': MagicMock(),
        'parse_youtube_playlist': MagicMock(),
        'get_sync_states': MagicMock(return_value={}),
        'set_db_update_automation_id': MagicMock(),
        'get_db_update_state': MagicMock(return_value={}),
        'db_update_lock': MagicMock(),
        'db_update_executor': MagicMock(),
        'run_db_update_task': MagicMock(),
        'run_deep_scan_task': MagicMock(),
        'get_duplicate_cleaner_state': MagicMock(return_value={}),
        'duplicate_cleaner_lock': MagicMock(),
        'duplicate_cleaner_executor': MagicMock(),
        'run_duplicate_cleaner': MagicMock(),
        'run_repair_job_now': MagicMock(),
        'download_orchestrator': MagicMock(),
        'run_async': MagicMock(),
        'tasks_lock': MagicMock(),
        'get_download_batches': MagicMock(return_value={}),
        'get_download_tasks': MagicMock(return_value={}),
        'sweep_empty_download_directories': MagicMock(return_value=0),
        'get_staging_path': MagicMock(return_value='/tmp'),
        'docker_resolve_path': MagicMock(side_effect=lambda p: p),
        'get_current_profile_id': MagicMock(return_value=1),
        'get_watchlist_scanner': MagicMock(),
        'get_app': MagicMock(),
        'get_beatport_data_cache': MagicMock(return_value={}),
        'init_automation_progress': MagicMock(),
        'record_progress_history': MagicMock(),
        'build_personalized_manager': MagicMock(),
    }
    defaults.update(overrides)
    return AutomationDeps(**defaults)


def test_sync_downloaded_playlists_action_registered():
    """Verify sync_downloaded_playlists is registered in register_all."""
    engine = MagicMock(spec=AutomationEngine)
    handlers = {}
    engine.register_action_handler.side_effect = lambda name, fn, *a, **kw: handlers.update({name: fn})

    deps = _make_deps(engine=engine)
    register_all(deps)

    assert 'sync_downloaded_playlists' in handlers
    assert callable(handlers['sync_downloaded_playlists'])


def test_sync_downloaded_playlists_in_blocks():
    """Verify sync_downloaded_playlists action block definition exists."""
    block = next((b for b in ACTIONS if b.get('type') == 'sync_downloaded_playlists'), None)
    assert block is not None
    assert block['label'] == 'Sync Downloaded Playlists'
    assert block['available'] is True


def test_sync_downloaded_playlists_in_system_automations():
    """Verify Auto-Sync Playlists After Database Update is seeded in SYSTEM_AUTOMATIONS."""
    spec = next((s for s in SYSTEM_AUTOMATIONS if s.get('action_type') == 'sync_downloaded_playlists'), None)
    assert spec is not None
    assert spec['name'] == 'Auto-Sync Playlists After Database Update'
    assert spec['trigger_type'] == 'database_update_completed'
    assert spec['trigger_config'] == {}


def test_handler_invokes_post_download_sync():
    """Verify auto_sync_downloaded_playlists calls sync_pending_downloaded_playlists with auto_id."""
    deps = _make_deps()
    with patch('core.automation.handlers.sync_downloaded_playlists.sync_pending_downloaded_playlists') as mock_sync:
        mock_sync.return_value = {'status': 'started'}
        res = auto_sync_downloaded_playlists({'_automation_id': 'auto-999'}, deps)
        assert res == {'status': 'started'}
        mock_sync.assert_called_once_with(deps, auto_id='auto-999')


def test_end_to_end_batch_complete_to_sync_chain():
    """Verify batch complete registers playlist and handler triggers auto_sync_playlist with skip_wishlist_add."""
    # 1. A playlist download batch finishes with successful downloads
    batch = {
        'mirrored_playlist_id': 101,
        'playlist_name': 'Weekend Grooves',
        'profile_id': 1,
    }
    register_completed_playlist_batch(batch)
    assert get_pending_playlist_syncs_count() == 1

    # 2. Database update finishes and emits database_update_completed -> handler executes
    deps = _make_deps()
    with patch('core.automation.handlers.sync_playlist.auto_sync_playlist') as mock_sync:
        mock_sync.return_value = {'status': 'started'}
        res = auto_sync_downloaded_playlists({'_automation_id': 'auto-sys-sync'}, deps)

        assert res['status'] == 'started'
        assert res['synced_playlists'] == ['Weekend Grooves']
        mock_sync.assert_called_once()
        config_passed = mock_sync.call_args[0][0]
        assert config_passed['playlist_id'] == '101'
        assert config_passed['skip_wishlist_add'] is True
        assert config_passed['force_sync'] is True

    # 3. Queue is drained
    assert get_pending_playlist_syncs_count() == 0
