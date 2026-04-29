from core.wishlist import processing
from core.wishlist.processing import WishlistManualDownloadRuntime


class _FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []
        self.debug_messages = []

    def info(self, msg):
        self.info_messages.append(msg)

    def warning(self, msg):
        self.warning_messages.append(msg)

    def error(self, msg):
        self.error_messages.append(msg)

    def debug(self, msg):
        self.debug_messages.append(msg)


class _FakeWishlistService:
    def __init__(self, tracks):
        self._tracks = list(tracks)
        self.removed_ids = set()
        self.duplicate_removals = []

    def remove_wishlist_duplicates(self, profile_id=1):
        self.duplicate_removals.append(profile_id)
        return 0

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return [
            track
            for track in self._tracks
            if (track.get("spotify_track_id") or track.get("id")) not in self.removed_ids
        ]

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
        self.removed_ids.add(spotify_track_id)
        return True


class _FakeMusicDatabase:
    def __init__(self, owned_matches=None):
        self.owned_matches = set(owned_matches or [])
        self.track_checks = []
        self.duplicate_removals = []

    def remove_wishlist_duplicates(self, profile_id=1):
        self.duplicate_removals.append(profile_id)
        return 0

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
        self.track_checks.append((track_name, artist_name, server_source, album))
        if (track_name, artist_name) in self.owned_matches:
            return {"id": "db-track"}, 0.9
        return None, 0.0


class _FakeExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, fn, *args, **kwargs):
        self.submissions.append((fn, args, kwargs))
        return object()


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _build_runtime(tracks, owned_matches=None, batch_map=None):
    wishlist_service = _FakeWishlistService(tracks)
    processing.get_wishlist_service = lambda: wishlist_service
    music_db = _FakeMusicDatabase(owned_matches=owned_matches)
    executor = _FakeExecutor()
    logger = _FakeLogger()
    activity_calls = []
    master_calls = []
    batch_map = batch_map or {}

    runtime = WishlistManualDownloadRuntime(
        get_music_database=lambda: music_db,
        download_batches=batch_map,
        tasks_lock=_FakeLock(),
        missing_download_executor=executor,
        run_full_missing_tracks_process=lambda *args, **kwargs: master_calls.append((args, kwargs)),
        get_batch_max_concurrent=lambda: 4,
        add_activity_item=lambda *args: activity_calls.append(args),
        active_server="navidrome",
        logger=logger,
        profile_id=1,
    )
    return runtime, wishlist_service, music_db, executor, logger, activity_calls, batch_map, master_calls


def _run_submitted_bg_job(executor):
    """Execute the bg job the executor received — simulates ThreadPoolExecutor."""
    assert len(executor.submissions) == 1, "expected exactly one bg submission"
    fn, args, kwargs = executor.submissions[0]
    fn(*args, **kwargs)


def test_start_manual_wishlist_download_batch_returns_immediately_with_placeholder():
    """Endpoint returns 200 immediately; cleanup runs in the bg job."""
    runtime, service, _db, executor, _logger, activity_calls, batch_map, master_calls = _build_runtime(
        tracks=[
            {
                "id": "track-1",
                "name": "Song 1",
                "artists": [{"name": "Artist 1"}],
                "album": {"name": "Album 1", "album_type": "album"},
            },
        ]
    )

    payload, status = processing.start_manual_wishlist_download_batch(runtime)

    # Synchronous response: 200 with batch_id, batch entry created with placeholder count.
    assert status == 200
    assert payload["success"] is True
    assert "batch_id" in payload
    assert batch_map[payload["batch_id"]]["analysis_total"] == 0  # placeholder
    assert batch_map[payload["batch_id"]]["phase"] == "analysis"
    assert batch_map[payload["batch_id"]]["force_download_all"] is True

    # Cleanup has NOT yet run (no DB calls, no master worker invocation).
    assert service.removed_ids == set()
    assert master_calls == []
    assert activity_calls == []

    # The bg job is queued.
    assert len(executor.submissions) == 1


def test_start_manual_wishlist_download_batch_filters_track_ids_and_starts_batch():
    runtime, _service, _db, executor, logger, activity_calls, batch_map, master_calls = _build_runtime(
        tracks=[
            {
                "id": "track-1",
                "name": "Song 1",
                "artists": [{"name": "Artist 1"}],
                "album": {"name": "Album 1", "album_type": "album"},
            },
            {
                "id": "track-2",
                "name": "Song 2",
                "artists": [{"name": "Artist 2"}],
                "album": {"name": "Album 2", "album_type": "album"},
            },
        ]
    )

    payload, status = processing.start_manual_wishlist_download_batch(
        runtime,
        track_ids=["track-2"],
        category=None,
    )

    assert status == 200
    assert payload["success"] is True
    assert "batch_id" in payload

    # Run the bg job that the executor would have run on a real thread.
    _run_submitted_bg_job(executor)

    assert activity_calls == [("", "Wishlist Download Started", "1 tracks", "Now")]
    assert len(master_calls) == 1
    master_args, _ = master_calls[0]
    assert master_args[1] == "wishlist"
    assert master_args[2][0]["id"] == "track-2"
    assert master_args[2][0]["_original_index"] == 0
    assert batch_map[payload["batch_id"]]["analysis_total"] == 1
    assert batch_map[payload["batch_id"]]["force_download_all"] is True
    assert any("Filtered to 1 specific tracks by ID" in msg for msg in logger.info_messages)


def test_start_manual_wishlist_download_batch_does_not_run_library_cleanup():
    """Manual flow does NOT scan the library for already-owned tracks.

    The batch sets force_download_all=True so owned tracks get downloaded
    anyway. Running remove_tracks_already_in_library here would just add a
    serial DB query per track (~30s on a 24-track wishlist) and contradict
    force_download_all. The standalone /api/wishlist/cleanup endpoint
    still exposes that pass for users who want explicit maintenance.
    """
    runtime, service, db, executor, _logger, activity_calls, batch_map, master_calls = _build_runtime(
        tracks=[
            {
                "id": "enhance-1",
                "name": "Enhance Song",
                "artists": [{"name": "Artist A"}],
                "album": {"name": "Enhance Album", "album_type": "album"},
                "source_type": "enhance",
            },
            {
                "id": "owned-1",
                "name": "Owned Song",
                "artists": [{"name": "Artist B"}],
                "album": {"name": "Owned Album", "album_type": "album"},
            },
        ],
        owned_matches={("Owned Song", "Artist B")},
    )

    payload, status = processing.start_manual_wishlist_download_batch(runtime)

    assert status == 200
    assert payload["success"] is True

    _run_submitted_bg_job(executor)

    # Owned-track removal pass does NOT run — wishlist still has the owned track.
    assert service.removed_ids == set()
    # The library check is skipped entirely — no per-track DB lookups.
    assert db.track_checks == []

    # All tracks are submitted to the master worker — including the "owned" one.
    assert len(master_calls) == 1
    master_args, _ = master_calls[0]
    assert [track["id"] for track in master_args[2]] == ["enhance-1", "owned-1"]
    assert batch_map[payload["batch_id"]]["analysis_total"] == 2
    assert activity_calls == [("", "Wishlist Download Started", "2 tracks", "Now")]


def test_bg_job_marks_batch_complete_when_wishlist_genuinely_empty():
    """If the wishlist is empty before the manual click, the bg job marks the batch complete."""
    runtime, _service, _db, executor, _logger, _activity, batch_map, master_calls = _build_runtime(
        tracks=[],
    )

    payload, status = processing.start_manual_wishlist_download_batch(runtime)
    assert status == 200

    _run_submitted_bg_job(executor)

    # No tracks → master worker never called, batch marked complete with explanatory error.
    assert master_calls == []
    assert batch_map[payload["batch_id"]]["phase"] == "complete"
    assert batch_map[payload["batch_id"]]["error"] == "No tracks in wishlist"
