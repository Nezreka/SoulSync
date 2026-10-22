"""Regression tests for Request List sync execution and parameter passing.

Covers:
1. api.source_playlists._run_sync_task forwards is_request_list and auto_remove_fulfilled.
2. core.automation.handlers.sync_playlist.auto_sync_playlist passes is_request_list and auto_remove_fulfilled.
3. safe thread runner in auto_sync_playlist catches unexpected errors and marks sync_states without hanging.
4. database backfill of upstream_playlist_id for existing request lists.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock
import pytest


def test_source_playlists_run_sync_task_forwards_request_list_args(monkeypatch):
    import api.source_playlists as sp

    captured_kwargs = {}

    def mock_discovery_run_sync_task(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {'status': 'ok'}

    monkeypatch.setattr(sp, "config_manager", MagicMock(get=lambda k, d=None: 'reconcile'))
    monkeypatch.setattr(sp, "_tracks_with_mirrored_quality_profile", lambda pid, name, t, profile_id: (t, None))
    monkeypatch.setattr(sp, "_build_sync_deps", lambda: MagicMock())
    monkeypatch.setattr(sp._discovery_sync, "run_sync_task", mock_discovery_run_sync_task)

    # Call with request list flags
    sp._run_sync_task(
        "auto_mirror_4145",
        "Liked Songs (Requests)",
        [],
        automation_id="auto-4145",
        profile_id=1,
        playlist_image_url="",
        sync_mode="reconcile",
        skip_wishlist_add=False,
        organize_by_playlist=False,
        user_initiated=True,
        is_request_list=True,
        auto_remove_fulfilled=True,
    )

    assert captured_kwargs.get("is_request_list") is True
    assert captured_kwargs.get("auto_remove_fulfilled") is True
    assert captured_kwargs.get("user_initiated") is True


def test_auto_sync_playlist_passes_request_list_flags(monkeypatch):
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    captured = []

    def mock_run_sync_task(*args, **kwargs):
        captured.append((args, kwargs))

    pl = {
        'id': 4145,
        'name': 'Liked Songs (Requests)',
        'profile_id': 1,
        'image_url': '',
        'organize_by_playlist': 0,
        'is_request_list': 1,
        'auto_remove_fulfilled': 1,
    }

    mock_db = MagicMock()
    mock_db.get_mirrored_playlist.return_value = pl
    mock_db.get_mirrored_playlist_tracks.return_value = [
        {
            'artist_name': 'Artist',
            'track_name': 'Track',
            'source_track_id': 'sp-1',
            'extra_data': json.dumps({'discovered': True, 'matched_data': {'id': 'sp-1', 'name': 'Track'}}),
        }
    ]

    deps = MagicMock()
    deps.get_database.return_value = mock_db
    deps.run_sync_task = mock_run_sync_task
    deps.load_sync_status_file.return_value = {}

    res = auto_sync_playlist({'playlist_id': '4145', 'user_initiated': True}, deps)
    assert res.get('status') == 'started'

    # Wait for thread to run
    for _ in range(50):
        if captured:
            break
        time.sleep(0.01)

    assert len(captured) == 1
    args, kwargs = captured[0]
    assert kwargs.get('is_request_list') is True
    assert kwargs.get('auto_remove_fulfilled') is True
    assert kwargs.get('user_initiated') is True


def test_auto_sync_playlist_safe_error_handler_marks_sync_states(monkeypatch):
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    sync_states = {}

    def broken_run_sync_task(*args, **kwargs):
        raise TypeError("Broken task unexpected argument")

    pl = {
        'id': 9999,
        'name': 'Fail Playlist',
        'profile_id': 1,
        'image_url': '',
        'is_request_list': 1,
    }

    mock_db = MagicMock()
    mock_db.get_mirrored_playlist.return_value = pl
    mock_db.get_mirrored_playlist_tracks.return_value = [
        {
            'artist_name': 'Artist',
            'track_name': 'Track',
            'source_track_id': 'sp-1',
            'extra_data': json.dumps({'discovered': True, 'matched_data': {'id': 'sp-1', 'name': 'Track'}}),
        }
    ]

    deps = MagicMock()
    deps.get_database.return_value = mock_db
    deps.run_sync_task = broken_run_sync_task
    deps.sync_states = sync_states
    deps.load_sync_status_file.return_value = {}

    res = auto_sync_playlist({'playlist_id': '9999', '_automation_id': 'auto-9999'}, deps)
    assert res.get('status') == 'started'

    # Wait for background thread
    for _ in range(50):
        if 'auto_mirror_9999' in sync_states:
            break
        time.sleep(0.01)

    assert 'auto_mirror_9999' in sync_states
    assert sync_states['auto_mirror_9999']['status'] == 'error'
    assert 'Broken task unexpected argument' in sync_states['auto_mirror_9999']['error']


def test_database_backfills_upstream_playlist_id(tmp_path):
    import sqlite3
    from database.music_database import MusicDatabase

    db_path = str(tmp_path / "test.db")
    db = MusicDatabase(db_path)

    # Insert a mirrored request list row with no upstream_playlist_id and a _req_ suffix
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO mirrored_playlists (
                source, source_playlist_id, name, is_request_list, upstream_playlist_id
            ) VALUES (?, ?, ?, ?, ?)
        """, ('spotify', 'spotify:liked-songs_req_9c9ed7ff', 'Liked Songs (Requests)', 1, None))
        conn.commit()

    # Re-run column/backfill migration
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        db._add_mirrored_playlist_request_list_columns(cursor)
        conn.commit()

    # Verify upstream_playlist_id is populated
    row = db.get_mirrored_playlist_by_source('spotify', 'spotify:liked-songs_req_9c9ed7ff')
    assert row is not None
    assert row['upstream_playlist_id'] == 'spotify:liked-songs'
