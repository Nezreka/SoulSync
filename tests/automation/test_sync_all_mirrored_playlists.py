"""Tests for 'Sync all mirrored playlists' in sync_playlist automation handler."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from core.automation.blocks import ACTIONS
from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.sync_playlist import auto_sync_playlist


class _StubLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _build_deps(**overrides):
    deps = MagicMock()
    deps.logger = _StubLogger()
    deps.load_sync_status_file.return_value = {}
    deps.get_sync_states.return_value = {}
    deps.config_manager.get.return_value = None
    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps


def test_sync_playlist_block_definition_exposes_all_field():
    sync_blocks = [b for b in ACTIONS if b['type'] == 'sync_playlist']
    assert len(sync_blocks) == 1
    block = sync_blocks[0]
    keys = {f['key']: f for f in block.get('config_fields', [])}
    assert 'playlist_id' in keys
    assert 'all' in keys
    assert keys['all']['type'] == 'checkbox'
    assert keys['all']['default'] is False


def test_auto_sync_playlist_all_no_playlists_found():
    mock_db = MagicMock()
    mock_db.get_mirrored_playlists.return_value = []
    deps = _build_deps(get_database=lambda: mock_db)

    result = auto_sync_playlist({'all': True}, deps)
    assert result == {'status': 'error', 'reason': 'No playlists found'}


def test_auto_sync_playlist_all_executes_sequentially():
    mock_db = MagicMock()
    pl1 = {
        'id': 101,
        'name': 'Rock Classics',
        'profile_id': 1,
        'image_url': '',
        'organize_by_playlist': 0,
    }
    pl2 = {
        'id': 102,
        'name': 'Jazz Vibes',
        'profile_id': 1,
        'image_url': '',
        'organize_by_playlist': 0,
    }
    mock_db.get_mirrored_playlists.return_value = [pl1, pl2]

    def _tracks_for(pid):
        return [
            {
                'id': pid * 10,
                'artist_name': f'Artist {pid}',
                'track_name': f'Song {pid}',
                'source_track_id': f'src-{pid}',
                'extra_data': json.dumps({'discovered': True, 'matched_data': {'id': f'disc-{pid}', 'name': f'Song {pid}'}}),
            }
        ]

    mock_db.get_mirrored_playlist_tracks.side_effect = lambda pid: _tracks_for(pid)

    synced_order = []
    sync_states_dict = {}

    def mock_run_sync_task(sync_id, sync_name, tracks_json, auto_id, *args, **kwargs):
        synced_order.append(sync_name)
        # Mark sync complete in sync_states
        sync_states_dict[sync_id] = {
            'status': 'finished',
            'playlist_name': sync_name,
            'result': {'matched_tracks': len(tracks_json)},
        }

    progress_calls = []

    def mock_update_progress(*args, **kwargs):
        progress_calls.append((args, kwargs))

    deps = _build_deps(
        get_database=lambda: mock_db,
        run_sync_task=mock_run_sync_task,
        update_progress=mock_update_progress,
        get_sync_states=lambda: sync_states_dict,
        load_sync_status_file=lambda: {},
    )

    res = auto_sync_playlist({'all': True, '_automation_id': 'auto-test-1'}, deps)
    assert res['status'] == 'started'
    assert res['playlist_count'] == '2'
    assert 'Rock Classics' in res['playlists']
    assert res['_manages_own_progress'] is True

    # Wait for the worker thread to finish
    deadline = time.time() + 5.0
    while len(synced_order) < 2 and time.time() < deadline:
        time.sleep(0.05)

    assert synced_order == ['Rock Classics', 'Jazz Vibes']

    # Wait briefly for final progress update
    time.sleep(0.1)
    final_statuses = [kw.get('status') for _, kw in progress_calls if 'status' in kw]
    assert 'finished' in final_statuses


def test_auto_sync_playlist_single_requires_playlist_id():
    mock_db = MagicMock()
    deps = _build_deps(get_database=lambda: mock_db)

    result = auto_sync_playlist({}, deps)
    assert result == {'status': 'error', 'reason': 'No playlist specified'}
