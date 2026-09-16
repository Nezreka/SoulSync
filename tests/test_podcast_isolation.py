import os
import sqlite3
import pytest
from unittest.mock import MagicMock

from core.downloads.lifecycle import is_music_batch, start_next_batch_of_downloads
from core.downloads.wishlist_failed import _process_failed_tracks_to_wishlist_exact
from core.wishlist.service import WishlistService
from database.music_database import MusicDatabase


def test_is_music_batch_detection():
    # Normal music batches
    assert is_music_batch("batch-123", {"playlist_id": "pl-1", "playlist_name": "Favorites"}) is True
    assert is_music_batch("album-456", {"is_album_download": True}) is True
    assert is_music_batch("manual-789", {}) is True

    # Podcast batches
    assert is_music_batch("podcasts", {}) is False
    assert is_music_batch("podcasts", None) is False
    assert is_music_batch("batch-pod", {"is_music": False}) is False
    assert is_music_batch("batch-pod", {"managed_externally": True}) is False
    assert is_music_batch("batch-pod", {"source_page": "Podcasts"}) is False
    assert is_music_batch("batch-pod", {"batch_type": "podcast"}) is False


def test_start_next_batch_of_downloads_skips_podcasts():
    from core.runtime_state import download_batches

    download_batches["podcasts"] = {
        "queue": ["task-1"],
        "active_count": 0,
        "max_concurrent": 2,
        "queue_index": 0,
        "source_page": "Podcasts",
        "is_music": False,
        "managed_externally": True,
    }

    deps = MagicMock()
    # Should exit early and never submit worker
    start_next_batch_of_downloads("podcasts", deps)
    deps.submit_download_track_worker.assert_not_called()
    assert download_batches["podcasts"]["active_count"] == 0
    assert download_batches["podcasts"]["queue_index"] == 0

    # Cleanup
    del download_batches["podcasts"]


def test_process_failed_tracks_to_wishlist_skips_podcasts():
    from core.runtime_state import download_batches

    download_batches["podcasts"] = {
        "queue": ["task-1"],
        "permanently_failed_tracks": [{"track_name": "Episode 1"}],
        "source_page": "Podcasts",
        "is_music": False,
        "managed_externally": True,
    }

    result = _process_failed_tracks_to_wishlist_exact("podcasts")
    assert result == {"tracks_added": 0, "errors": 0}

    # Cleanup
    del download_batches["podcasts"]


def test_wishlist_service_rejects_podcast_tracks():
    db_mock = MagicMock()
    service = WishlistService()
    service._database = db_mock

    # Podcast with source_type = "podcast"
    res1 = service.add_failed_track_from_modal(
        {"track_name": "Ep 1"},
        source_type="podcast",
    )
    assert res1 is False
    db_mock.add_to_wishlist.assert_not_called()

    # Podcast with download_source = "Podcast"
    res2 = service.add_failed_track_from_modal(
        {"track_name": "Ep 2", "download_source": "Podcast"},
        source_type="manual",
    )
    assert res2 is False
    db_mock.add_to_wishlist.assert_not_called()

    # Podcast with source_context source_page = "Podcasts"
    res3 = service.add_failed_track_from_modal(
        {"track_name": "Ep 3"},
        source_context={"source_page": "Podcasts"},
    )
    assert res3 is False
    db_mock.add_to_wishlist.assert_not_called()


def test_music_database_rejects_and_purges_podcasts(tmp_path):
    db_file = tmp_path / "test_music.db"
    db = MusicDatabase(str(db_file))

    # Try adding podcast track directly to wishlist
    outcome = db.add_to_wishlist_detailed(
        spotify_track_data={"id": "podcast-12345", "name": "Episode 1", "artists": [{"name": "Host"}]},
        source_type="podcast",
    )
    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "podcast_not_eligible"

    # Try adding normal music track to wishlist
    normal_outcome = db.add_to_wishlist_detailed(
        spotify_track_data={"id": "spotify-song-1", "name": "Real Song", "artists": [{"name": "Real Artist"}]},
        source_type="playlist",
    )
    assert normal_outcome["status"] == "created"

    # Simulate legacy stray podcast in database
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, source_type, source_info)
            VALUES (?, ?, ?, ?)
            """,
            ("podcast-legacy-999", '{"name": "Old Pod"}', "podcast", '{"source_page": "Podcasts"}'),
        )
        conn.commit()

    # Purge should remove the podcast and keep the normal music track
    purged = db.purge_podcast_tracks_from_wishlist()
    assert purged >= 1

    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT spotify_track_id FROM wishlist_tracks")
        ids = [row[0] for row in cursor.fetchall()]
        assert "spotify-song-1" in ids
        assert "podcast-legacy-999" not in ids
        assert "podcast-12345" not in ids


def test_music_batch_starts_workers():
    from core.runtime_state import download_batches, download_tasks

    download_batches["batch-music-test"] = {
        "queue": ["task-music-1"],
        "active_count": 0,
        "max_concurrent": 2,
        "queue_index": 0,
        "playlist_id": "pl-123",
        "playlist_name": "Rock Hits",
        "phase": "downloading",
    }
    download_tasks["task-music-1"] = {"status": "queued"}

    deps = MagicMock()
    deps.is_shutting_down.return_value = False
    deps.get_global_max_concurrent = MagicMock(return_value=None)

    start_next_batch_of_downloads("batch-music-test", deps)
    deps.submit_download_track_worker.assert_called_once_with("task-music-1", "batch-music-test")
    assert download_batches["batch-music-test"]["active_count"] == 1
    assert download_batches["batch-music-test"]["queue_index"] == 1

    del download_batches["batch-music-test"]
    del download_tasks["task-music-1"]


def test_music_track_failure_adds_to_wishlist_in_db(tmp_path):
    db_file = tmp_path / "test_music.db"
    db = MusicDatabase(str(db_file))

    service = WishlistService()
    service._database = db

    failed_track_info = {
        "id": "spotify-track-fail-1",
        "name": "Failed Song",
        "track_name": "Failed Song",
        "artist": "Rock Band",
        "artists": [{"name": "Rock Band"}],
        "failure_reason": "All download sources exhausted",
        "download_source": "Soulseek",
        "origin": "spotify",
    }

    added = service.add_failed_track_from_modal(
        failed_track_info,
        source_type="playlist",
        source_context={"playlist_name": "My Playlist", "playlist_id": "pl-123"},
    )
    assert added is True

    # Verify track now exists in the database wishlist
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT spotify_track_id, source_type, spotify_data FROM wishlist_tracks WHERE spotify_track_id = 'spotify-track-fail-1'")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "spotify-track-fail-1"
        assert row[1] == "playlist"
        assert "Failed Song" in row[2]


