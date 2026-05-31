"""Tests for core/library/manual_library_match.py and DB methods."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.library import manual_library_match as mlm
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


# ---------------------------------------------------------------------------
# normalize_library_track_id — regression guard for issue #754
# ("Invalid library track id" on Jellyfin/Navidrome/Subsonic servers)
# ---------------------------------------------------------------------------

def test_normalize_passes_numeric_plex_id():
    # Plex ratingKeys are numeric strings — must survive unchanged (not int-ified).
    assert mlm.normalize_library_track_id("12345") == "12345"
    assert mlm.normalize_library_track_id(12345) == "12345"


def test_normalize_passes_guid_id():
    # The #754 bug: Jellyfin/Navidrome ids are non-numeric. int() rejected them.
    assert mlm.normalize_library_track_id("a1b2c3d4-e5f6") == "a1b2c3d4-e5f6"
    assert mlm.normalize_library_track_id("Do I Wanna Know_opus") == "Do I Wanna Know_opus"


def test_normalize_trims_whitespace():
    assert mlm.normalize_library_track_id("  guid-123  ") == "guid-123"


def test_normalize_rejects_empty_and_none():
    assert mlm.normalize_library_track_id(None) is None
    assert mlm.normalize_library_track_id("") is None
    assert mlm.normalize_library_track_id("   ") is None


def test_guid_library_track_id_round_trips(db):
    """End-to-end regression for #754: a non-numeric library id must save,
    read back identically, and enrich — never get coerced or rejected."""
    guid = "a1b2c3d4e5f6-jellyfin"
    norm = mlm.normalize_library_track_id(guid)
    assert norm == guid
    ok = db.save_manual_library_match(1, "spotify", "src-track-1", norm,
                                       source_title="Do I Wanna Know?",
                                       source_artist="Arctic Monkeys")
    assert ok is True
    row = db.get_manual_library_match(1, "spotify", "src-track-1")
    assert row is not None
    assert row["library_track_id"] == guid  # stored as-is, not mangled to int

    # Enrichment must resolve the GUID against tracks.id (TEXT) without error.
    with patch.object(db, "api_get_tracks_by_ids", return_value=[
            {"title": "Do I Wanna Know?", "artist_name": "Arctic Monkeys",
             "album_title": "AM", "file_path": "/m/x.opus", "bitrate": 196}]) as mock_get:
        enriched = mlm._enrich_match(row, db)
    mock_get.assert_called_once_with([guid])  # passes the string id straight through
    assert enriched["library_title"] == "Do I Wanna Know?"


# ---------------------------------------------------------------------------
# DB-layer tests
# ---------------------------------------------------------------------------

def test_save_and_get_roundtrip(db):
    ok = db.save_manual_library_match(1, "spotify", "track-abc", 42,
                                       source_title="HUMBLE.", source_artist="Kendrick Lamar",
                                       source_album="DAMN.", server_source="")
    assert ok is True
    row = db.get_manual_library_match(1, "spotify", "track-abc")
    assert row is not None
    assert row["library_track_id"] == 42
    assert row["source_title"] == "HUMBLE."
    assert row["source_artist"] == "Kendrick Lamar"


def test_save_upserts_existing(db):
    db.save_manual_library_match(1, "spotify", "track-abc", 42)
    db.save_manual_library_match(1, "spotify", "track-abc", 99, source_title="Updated")
    row = db.get_manual_library_match(1, "spotify", "track-abc")
    assert row["library_track_id"] == 99
    assert row["source_title"] == "Updated"


def test_delete_by_id(db):
    db.save_manual_library_match(1, "spotify", "track-abc", 42)
    row = db.get_manual_library_match(1, "spotify", "track-abc")
    assert row is not None
    ok = db.delete_manual_library_match(row["id"], 1)
    assert ok is True
    assert db.get_manual_library_match(1, "spotify", "track-abc") is None


def test_list_matches_scoped_to_profile(db):
    db.save_manual_library_match(1, "spotify", "t1", 10)
    db.save_manual_library_match(1, "spotify", "t2", 20)
    rows = db.list_manual_library_matches(1)
    assert len(rows) == 2
    # Ordered by updated_at DESC — most recent first
    ids = {r["library_track_id"] for r in rows}
    assert ids == {10, 20}


def test_profile_isolation(db):
    db.save_manual_library_match(1, "spotify", "track-abc", 10)
    db.save_manual_library_match(2, "spotify", "track-abc", 20)
    assert db.get_manual_library_match(1, "spotify", "track-abc")["library_track_id"] == 10
    assert db.get_manual_library_match(2, "spotify", "track-abc")["library_track_id"] == 20
    assert len(db.list_manual_library_matches(1)) == 1
    assert len(db.list_manual_library_matches(2)) == 1


def test_server_source_isolation(db):
    db.save_manual_library_match(1, "spotify", "track-abc", 10, server_source="plex")
    db.save_manual_library_match(1, "spotify", "track-abc", 20, server_source="jellyfin")
    assert db.get_manual_library_match(1, "spotify", "track-abc", "plex")["library_track_id"] == 10
    assert db.get_manual_library_match(1, "spotify", "track-abc", "jellyfin")["library_track_id"] == 20


def test_get_returns_none_when_absent(db):
    assert db.get_manual_library_match(1, "spotify", "nonexistent") is None


def test_get_match_for_track_falls_back_across_source_labels(db):
    db.save_manual_library_match(
        1,
        "mirrored",
        "track-abc",
        42,
        source_title="Coffee Break",
        source_artist="Zeds Dead",
    )

    row = mlm.get_match_for_track(
        db,
        1,
        {
            "id": "track-abc",
            "name": "Coffee Break",
            "artists": [{"name": "Zeds Dead"}],
            "provider": "wishlist",
        },
        default_source="wishlist",
    )

    assert row is not None
    assert row["library_track_id"] == 42


def test_get_match_for_track_falls_back_to_source_title_artist(db):
    db.save_manual_library_match(
        1,
        "mirrored",
        "old-id",
        42,
        source_title="Coffee Break",
        source_artist="Zeds Dead",
    )

    row = mlm.get_match_for_track(
        db,
        1,
        {
            "id": "new-id",
            "name": "Coffee Break",
            "artists": [{"name": "Zeds Dead"}],
            "provider": "musicbrainz",
        },
        default_source="wishlist",
    )

    assert row is not None
    assert row["library_track_id"] == 42


def test_add_to_wishlist_skips_manual_matched_track(db):
    db.save_manual_library_match(1, "spotify", "track-abc", 42)

    ok = db.add_to_wishlist(
        track_data={
            "id": "track-abc",
            "name": "HUMBLE.",
            "artists": [{"name": "Kendrick Lamar"}],
            "album": {"name": "DAMN."},
            "provider": "spotify",
        },
        failure_reason="Download failed",
        profile_id=1,
    )

    assert ok is True
    assert db.get_wishlist_tracks(profile_id=1) == []


def test_add_to_wishlist_skips_manual_match_saved_from_mirrored_source(db):
    db.save_manual_library_match(
        1,
        "mirrored",
        "track-abc",
        42,
        source_title="Coffee Break",
        source_artist="Zeds Dead",
    )

    ok = db.add_to_wishlist(
        track_data={
            "id": "track-abc",
            "name": "Coffee Break",
            "artists": [{"name": "Zeds Dead"}],
            "album": {"name": "Coffee Break"},
            "provider": "wishlist",
        },
        failure_reason="Download failed",
        profile_id=1,
    )

    assert ok is True
    assert db.get_wishlist_tracks(profile_id=1) == []


def test_get_match_returns_none_when_db_lacks_manual_match_method():
    class _MinimalDB:
        pass

    assert mlm.get_match(_MinimalDB(), 1, "spotify", "track-abc") is None


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------

def test_service_save_and_get(db):
    mlm.save_match(db, 1, "spotify", "t1", 42, source_title="Song A")
    row = mlm.get_match(db, 1, "spotify", "t1")
    assert row is not None
    assert row["library_track_id"] == 42


def test_service_delete(db):
    mlm.save_match(db, 1, "spotify", "t1", 42)
    row = mlm.get_match(db, 1, "spotify", "t1")
    mlm.delete_match(db, row["id"], 1)
    assert mlm.get_match(db, 1, "spotify", "t1") is None


def test_service_list_enriches(db):
    mlm.save_match(db, 1, "spotify", "t1", 42, source_title="Song A", source_artist="Artist X")
    with patch.object(db, "api_get_tracks_by_ids", return_value=[{"title": "Song A", "artist_name": "Artist X", "album_title": "Album Z", "file_path": "/music/a.flac", "bitrate": 320}]):
        matches = mlm.list_matches(db, 1)
    assert len(matches) == 1
    assert matches[0]["library_title"] == "Song A"


def test_search_library_candidates(db):
    with patch.object(db, "api_search_tracks", return_value=[{"id": 1, "title": "HUMBLE.", "artist_name": "Kendrick Lamar"}]) as mock_search:
        results = mlm.search_library_candidates(db, "HUMBLE")
    # searches by title AND artist, deduped
    assert mock_search.call_count == 2
    assert len(results) == 1
    assert results[0]["title"] == "HUMBLE."


def test_search_source_candidates_empty_query(db):
    results = mlm.search_source_candidates(db, "", 1)
    assert results == []


# ---------------------------------------------------------------------------
# Integration: wishlist processing
# ---------------------------------------------------------------------------

def test_wishlist_skips_manual_matched_track():
    """Manual match causes track to be removed from wishlist without fuzzy check."""
    track = {
        "name": "HUMBLE.",
        "artists": [{"name": "Kendrick Lamar"}],
        "spotify_track_id": "spotify-track-123",
    }

    mock_wishlist_svc = MagicMock()
    mock_wishlist_svc.get_wishlist_tracks_for_download.return_value = [track]
    mock_wishlist_svc.mark_track_download_result.return_value = True

    mock_profiles_db = MagicMock()
    mock_profiles_db.get_all_profiles.return_value = [{"id": 1}]

    mock_music_db = MagicMock()
    mock_music_db.check_track_exists = MagicMock()

    with patch("core.library.manual_library_match.get_match_for_track", return_value={"id": 1, "library_track_id": 42}):
        from core.wishlist.processing import remove_tracks_already_in_library
        removed = remove_tracks_already_in_library(
            mock_wishlist_svc,
            mock_profiles_db,
            mock_music_db,
            active_server="plex",
        )

    assert removed == 1
    mock_wishlist_svc.mark_track_download_result.assert_called_once_with("spotify-track-123", success=True)
    mock_music_db.check_track_exists.assert_not_called()


def test_wishlist_falls_through_when_no_match():
    """No manual match → fuzzy path runs normally."""
    track = {
        "name": "HUMBLE.",
        "artists": [{"name": "Kendrick Lamar"}],
        "spotify_track_id": "spotify-track-123",
    }

    mock_wishlist_svc = MagicMock()
    mock_wishlist_svc.get_wishlist_tracks_for_download.return_value = [track]
    mock_wishlist_svc.mark_track_download_result.return_value = False

    mock_profiles_db = MagicMock()
    mock_profiles_db.get_all_profiles.return_value = [{"id": 1}]

    mock_music_db = MagicMock()
    mock_music_db.check_track_exists.return_value = (None, 0.0)

    with patch("core.library.manual_library_match.get_match_for_track", return_value=None):
        from core.wishlist.processing import remove_tracks_already_in_library
        removed = remove_tracks_already_in_library(
            mock_wishlist_svc,
            mock_profiles_db,
            mock_music_db,
            active_server="plex",
        )

    assert removed == 0
    mock_music_db.check_track_exists.assert_called()


# ---------------------------------------------------------------------------
# Integration: downloads/master analysis loop
# ---------------------------------------------------------------------------

def test_master_analysis_marks_found():
    """Manual match causes the real analysis loop to mark track found=True."""
    from core.downloads.master import MasterDeps, run_full_missing_tracks_process
    from core.runtime_state import download_batches, tasks_lock
    import threading

    batch_id = "test-batch-real-123"
    track_data = {"name": "HUMBLE.", "artists": [], "id": "spotify-track-abc"}

    with tasks_lock:
        download_batches[batch_id] = {
            "phase": "analysis",
            "analysis_total": 1,
            "analysis_processed": 0,
            "force_download_all": False,
            "is_album_download": False,
            "album_context": None,
            "artist_context": None,
            "profile_id": 1,
            "batch_source": "spotify",
            "wing_it": False,
            "playlist_folder_mode": False,
            "queue": [],
            "active_count": 0,
            "max_concurrent": 1,
            "queue_index": 0,
            "permanently_failed_tracks": [],
            "cancelled_tracks": set(),
            "analysis_results": [],
        }

    mock_db = MagicMock()
    mock_db.check_track_exists.return_value = (None, 0.0)
    mock_db.update_sync_history_completion = MagicMock()

    mock_deps = MagicMock()
    mock_deps.config_manager.get.return_value = False
    mock_deps.config_manager.get_active_media_server.return_value = "plex"
    mock_deps.mb_worker = None
    mock_deps.mb_release_cache = {}
    mock_deps.mb_release_cache_lock = threading.Lock()
    mock_deps.mb_release_detail_cache = {}
    mock_deps.mb_release_detail_cache_lock = threading.Lock()
    mock_deps.normalize_album_cache_key = lambda x: x.lower().strip()
    mock_deps.check_and_remove_track_from_wishlist_by_metadata = MagicMock()
    mock_deps.is_explicit_blocked = MagicMock(return_value=False)
    mock_deps.youtube_playlist_states = {}
    mock_deps.tidal_discovery_states = {}
    mock_deps.deezer_discovery_states = {}
    mock_deps.spotify_public_discovery_states = {}
    mock_deps.missing_download_executor = MagicMock()
    mock_deps.process_failed_tracks_to_wishlist_exact_with_auto_completion = MagicMock()
    mock_deps.source_reuse_logger = MagicMock()
    mock_deps.download_monitor = MagicMock()
    mock_deps.start_next_batch_of_downloads = MagicMock()
    mock_deps.reset_wishlist_auto_processing = MagicMock()

    with patch("core.library.manual_library_match.get_match_for_track", return_value={"id": 1, "library_track_id": 42}), \
         patch("database.music_database.MusicDatabase", return_value=mock_db):
        run_full_missing_tracks_process(batch_id, "playlist-1", [track_data], mock_deps)

    with tasks_lock:
        results = download_batches.get(batch_id, {}).get("analysis_results", [])
        download_batches.pop(batch_id, None)

    assert len(results) == 1
    assert results[0]["found"] is True
    assert results[0]["match_reason"] == "manual_library_match"
    mock_deps.check_and_remove_track_from_wishlist_by_metadata.assert_called_once_with(track_data)


def test_master_analysis_manual_match_wins_over_internal_force_download():
    """Manual match overrides internal force_download_all used by wishlist batches."""
    from core.downloads.master import run_full_missing_tracks_process
    from core.runtime_state import download_batches, tasks_lock
    import threading

    batch_id = "test-batch-force-456"
    track_data = {"name": "HUMBLE.", "artists": [], "id": "spotify-track-abc"}

    with tasks_lock:
        download_batches[batch_id] = {
            "phase": "analysis",
            "analysis_total": 1,
            "analysis_processed": 0,
            "force_download_all": True,  # would normally bypass DB check and queue download
            "ignore_manual_matches": False,
            "is_album_download": False,
            "album_context": None,
            "artist_context": None,
            "profile_id": 1,
            "batch_source": "spotify",
            "wing_it": False,
            "playlist_folder_mode": False,
            "queue": [],
            "active_count": 0,
            "max_concurrent": 1,
            "queue_index": 0,
            "permanently_failed_tracks": [],
            "cancelled_tracks": set(),
            "analysis_results": [],
        }

    mock_db = MagicMock()
    mock_db.check_track_exists.return_value = (None, 0.0)
    mock_db.update_sync_history_completion = MagicMock()

    mock_deps = MagicMock()
    mock_deps.config_manager.get.return_value = False
    mock_deps.config_manager.get_active_media_server.return_value = "plex"
    mock_deps.mb_worker = None
    mock_deps.mb_release_cache = {}
    mock_deps.mb_release_cache_lock = threading.Lock()
    mock_deps.mb_release_detail_cache = {}
    mock_deps.mb_release_detail_cache_lock = threading.Lock()
    mock_deps.normalize_album_cache_key = lambda x: x.lower().strip()
    mock_deps.check_and_remove_track_from_wishlist_by_metadata = MagicMock()
    mock_deps.is_explicit_blocked = MagicMock(return_value=False)
    mock_deps.youtube_playlist_states = {}
    mock_deps.tidal_discovery_states = {}
    mock_deps.deezer_discovery_states = {}
    mock_deps.spotify_public_discovery_states = {}
    mock_deps.missing_download_executor = MagicMock()
    mock_deps.process_failed_tracks_to_wishlist_exact_with_auto_completion = MagicMock()
    mock_deps.source_reuse_logger = MagicMock()
    mock_deps.download_monitor = MagicMock()
    mock_deps.start_next_batch_of_downloads = MagicMock()
    mock_deps.reset_wishlist_auto_processing = MagicMock()

    with patch("core.library.manual_library_match.get_match_for_track", return_value={"id": 1, "library_track_id": 42}), \
         patch("database.music_database.MusicDatabase", return_value=mock_db):
        run_full_missing_tracks_process(batch_id, "playlist-1", [track_data], mock_deps)

    with tasks_lock:
        results = download_batches.get(batch_id, {}).get("analysis_results", [])
        queue = download_batches.get(batch_id, {}).get("queue", [])
        download_batches.pop(batch_id, None)

    assert len(results) == 1
    assert results[0]["found"] is True
    assert results[0]["match_reason"] == "manual_library_match"
    # Track must NOT enter the download queue despite internal force_download_all=True
    assert queue == []
    mock_deps.check_and_remove_track_from_wishlist_by_metadata.assert_called_once_with(track_data)
