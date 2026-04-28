"""Tests for core/downloads/history.py — sync history start/completion + source detection."""

from __future__ import annotations

import json

import pytest

from core.downloads import history
from core.runtime_state import download_tasks
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture(autouse=True)
def clear_tasks():
    """Each test gets a clean download_tasks dict."""
    download_tasks.clear()
    yield
    download_tasks.clear()


# ---------------------------------------------------------------------------
# detect_sync_source
# ---------------------------------------------------------------------------

def test_detect_source_wishlist():
    assert history.detect_sync_source('wishlist') == 'wishlist'


def test_detect_source_default_spotify():
    assert history.detect_sync_source('something_unknown') == 'spotify'


def test_detect_source_youtube_prefix():
    assert history.detect_sync_source('youtube_abc123') == 'youtube'


def test_detect_source_tidal_prefix():
    assert history.detect_sync_source('tidal_xyz') == 'tidal'


def test_detect_source_deezer_prefix():
    assert history.detect_sync_source('deezer_xyz') == 'deezer'


def test_detect_source_beatport_prefix():
    assert history.detect_sync_source('beatport_anything') == 'beatport'


def test_detect_source_listenbrainz_prefix():
    assert history.detect_sync_source('listenbrainz_mbid') == 'listenbrainz'


def test_detect_source_mirrored_auto_prefix():
    assert history.detect_sync_source('auto_mirror_pl1') == 'mirrored'


def test_detect_source_youtube_mirrored_takes_precedence_over_youtube():
    """Both prefixes match — youtube_mirrored_ must win."""
    assert history.detect_sync_source('youtube_mirrored_pl1') == 'mirrored'


def test_detect_source_discover_album():
    assert history.detect_sync_source('discover_album_x') == 'discover'


def test_detect_source_seasonal_album():
    assert history.detect_sync_source('seasonal_album_x') == 'discover'


def test_detect_source_library():
    assert history.detect_sync_source('library_redownload_id') == 'library'


def test_detect_source_issue_download():
    assert history.detect_sync_source('issue_download_id') == 'library'


def test_detect_source_artist_album():
    assert history.detect_sync_source('artist_album_xyz') == 'spotify'


def test_detect_source_enhanced_search():
    assert history.detect_sync_source('enhanced_search_xyz') == 'spotify'


def test_detect_source_spotify_public():
    assert history.detect_sync_source('spotify_public_xyz') == 'spotify_public'


def test_detect_source_beatport_release():
    assert history.detect_sync_source('beatport_release_x') == 'beatport'


# ---------------------------------------------------------------------------
# record_sync_history_start — happy paths
# ---------------------------------------------------------------------------

def test_start_records_basic_playlist(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='My PL',
        tracks=[{'name': 't1'}, {'name': 't2'}],
        is_album_download=False, album_context=None, artist_context=None,
        playlist_folder_mode=False,
    )
    rows = db.get_latest_sync_history_by_playlist('spot_pl')
    assert rows is not None
    assert rows['batch_id'] == 'b1'
    assert rows['playlist_name'] == 'My PL'
    assert rows['source'] == 'spotify'
    assert rows['sync_type'] == 'playlist'
    assert rows['total_tracks'] == 2


def test_start_album_sets_sync_type_album(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='Alb',
        tracks=[{'name': 't1'}],
        is_album_download=True, album_context=None, artist_context=None,
        playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['sync_type'] == 'album'


def test_start_wishlist_sets_sync_type_wishlist(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='wishlist', playlist_name='Wishlist',
        tracks=[],
        is_album_download=False, album_context=None, artist_context=None,
        playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('wishlist')
    assert row['sync_type'] == 'wishlist'


def test_start_pulls_thumb_from_album_context_images_list(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='Alb',
        tracks=[],
        is_album_download=True,
        album_context={'images': [{'url': 'http://thumb.jpg'}]},
        artist_context=None, playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['thumb_url'] == 'http://thumb.jpg'


def test_start_pulls_thumb_from_album_context_image_url_fallback(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='Alb',
        tracks=[],
        is_album_download=True,
        album_context={'image_url': 'http://x.jpg'},
        artist_context=None, playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['thumb_url'] == 'http://x.jpg'


def test_start_pulls_thumb_from_first_track_when_album_context_missing(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='PL',
        tracks=[{'album': {'images': [{'url': 'http://track.jpg'}]}}],
        is_album_download=False, album_context=None, artist_context=None,
        playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['thumb_url'] == 'http://track.jpg'


def test_start_no_thumb_anywhere_leaves_null(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='PL',
        tracks=[], is_album_download=False,
        album_context=None, artist_context=None, playlist_folder_mode=False,
    )
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['thumb_url'] is None


def test_start_updates_existing_entry_for_same_playlist_id(db):
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='spot_pl', playlist_name='Original',
        tracks=[{'name': 'a'}], is_album_download=False,
        album_context=None, artist_context=None, playlist_folder_mode=False,
    )
    first_row = db.get_latest_sync_history_by_playlist('spot_pl')

    history.record_sync_history_start(
        db, batch_id='b2', playlist_id='spot_pl', playlist_name='Renamed',
        tracks=[{'name': 'a'}, {'name': 'b'}, {'name': 'c'}], is_album_download=False,
        album_context=None, artist_context=None, playlist_folder_mode=False,
    )
    second_row = db.get_latest_sync_history_by_playlist('spot_pl')
    # Same row id (updated, not duplicated)
    assert second_row['id'] == first_row['id']
    assert second_row['batch_id'] == 'b2'
    assert second_row['playlist_name'] == 'Renamed'
    assert second_row['total_tracks'] == 3


def test_start_swallows_db_error(db, monkeypatch):
    """Best-effort: must not raise if DB write fails."""
    def boom(*a, **kw):
        raise RuntimeError("db dead")
    monkeypatch.setattr(db, 'add_sync_history_entry', boom)
    # Must not raise
    history.record_sync_history_start(
        db, batch_id='b1', playlist_id='new_pl', playlist_name='X',
        tracks=[], is_album_download=False,
        album_context=None, artist_context=None, playlist_folder_mode=False,
    )


# ---------------------------------------------------------------------------
# record_sync_history_completion
# ---------------------------------------------------------------------------

def _seed_start(db, batch_id='b1', playlist_id='spot_pl'):
    history.record_sync_history_start(
        db, batch_id=batch_id, playlist_id=playlist_id, playlist_name='PL',
        tracks=[], is_album_download=False,
        album_context=None, artist_context=None, playlist_folder_mode=False,
    )


def test_completion_writes_counts(db):
    _seed_start(db)
    download_tasks['t1'] = {'track_index': 0, 'status': 'completed'}
    download_tasks['t2'] = {'track_index': 1, 'status': 'failed'}
    batch = {
        'queue': ['t1', 't2'],
        'analysis_results': [
            {'track_index': 0, 'found': True, 'confidence': 0.95, 'track': {'name': 'A'}},
            {'track_index': 1, 'found': False, 'confidence': 0.0, 'track': {'name': 'B'}},
        ],
        'permanently_failed_tracks': ['t2'],
    }
    history.record_sync_history_completion(db, 'b1', batch)

    row = db.get_latest_sync_history_by_playlist('spot_pl')
    assert row['tracks_found'] == 1
    assert row['tracks_downloaded'] == 1
    assert row['tracks_failed'] == 1


def test_completion_per_track_results_json(db):
    _seed_start(db)
    download_tasks['t1'] = {'track_index': 0, 'status': 'completed'}
    batch = {
        'queue': ['t1'],
        'analysis_results': [{
            'track_index': 0,
            'found': True,
            'confidence': 0.876543,
            'track': {
                'name': 'Money',
                'artists': [{'name': 'Pink Floyd'}],
                'album': {'name': 'DSOTM', 'images': [{'url': 'http://thumb.jpg'}]},
                'duration_ms': 383000,
                'id': 'spotify:track:xyz',
            },
        }],
        'permanently_failed_tracks': [],
    }
    history.record_sync_history_completion(db, 'b1', batch)

    row = db.get_latest_sync_history_by_playlist('spot_pl')
    track_results = json.loads(row['track_results'])
    assert len(track_results) == 1
    entry = track_results[0]
    assert entry['index'] == 0
    assert entry['name'] == 'Money'
    assert entry['artist'] == 'Pink Floyd'
    assert entry['album'] == 'DSOTM'
    assert entry['image_url'] == 'http://thumb.jpg'
    assert entry['duration_ms'] == 383000
    assert entry['source_track_id'] == 'spotify:track:xyz'
    assert entry['status'] == 'found'
    assert entry['confidence'] == 0.877  # rounded to 3
    assert entry['matched_track'] is None
    assert entry['download_status'] == 'completed'


def test_completion_artist_string_form_normalized(db):
    _seed_start(db)
    download_tasks['t1'] = {'track_index': 0, 'status': 'completed'}
    batch = {
        'queue': ['t1'],
        'analysis_results': [{
            'track_index': 0, 'found': True, 'confidence': 1.0,
            'track': {'name': 'X', 'artists': ['Plain String Artist'], 'album': 'StringAlbum'},
        }],
        'permanently_failed_tracks': [],
    }
    history.record_sync_history_completion(db, 'b1', batch)
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    entry = json.loads(row['track_results'])[0]
    assert entry['artist'] == 'Plain String Artist'
    assert entry['album'] == 'StringAlbum'


def test_completion_no_artists_returns_empty_string(db):
    _seed_start(db)
    download_tasks['t1'] = {'track_index': 0, 'status': 'completed'}
    batch = {
        'queue': ['t1'],
        'analysis_results': [{
            'track_index': 0, 'found': True, 'confidence': 1.0,
            'track': {'name': 'X', 'artists': []},
        }],
        'permanently_failed_tracks': [],
    }
    history.record_sync_history_completion(db, 'b1', batch)
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    entry = json.loads(row['track_results'])[0]
    assert entry['artist'] == ''


def test_completion_unmatched_tracks_marked_not_found(db):
    _seed_start(db)
    batch = {
        'queue': [],
        'analysis_results': [{
            'track_index': 0, 'found': False, 'confidence': 0.0,
            'track': {'name': 'X'},
        }],
        'permanently_failed_tracks': [],
    }
    history.record_sync_history_completion(db, 'b1', batch)
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    entry = json.loads(row['track_results'])[0]
    assert entry['status'] == 'not_found'


def test_completion_swallows_db_error(db, monkeypatch):
    _seed_start(db)
    def boom(*a, **kw):
        raise RuntimeError("db dead")
    monkeypatch.setattr(db, 'update_sync_history_completion', boom)
    # Must not raise
    history.record_sync_history_completion(db, 'b1', {
        'queue': [], 'analysis_results': [], 'permanently_failed_tracks': [],
    })


def test_completion_no_track_results_skips_track_results_write(db, monkeypatch):
    _seed_start(db)
    calls = []
    monkeypatch.setattr(db, 'update_sync_history_track_results',
                         lambda *a, **kw: calls.append((a, kw)))
    history.record_sync_history_completion(db, 'b1', {
        'queue': [], 'analysis_results': [], 'permanently_failed_tracks': [],
    })
    assert calls == []


def test_completion_download_status_map_falls_through_to_unknown(db):
    _seed_start(db)
    # Task exists in queue but no status field
    download_tasks['t1'] = {'track_index': 0}
    batch = {
        'queue': ['t1'],
        'analysis_results': [{
            'track_index': 0, 'found': True, 'confidence': 1.0,
            'track': {'name': 'X'},
        }],
        'permanently_failed_tracks': [],
    }
    history.record_sync_history_completion(db, 'b1', batch)
    row = db.get_latest_sync_history_by_playlist('spot_pl')
    entry = json.loads(row['track_results'])[0]
    assert entry['download_status'] == 'unknown'
