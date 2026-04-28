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
    music_db = _FakeMusicDatabase(owned_matches=owned_matches)
    executor = _FakeExecutor()
    logger = _FakeLogger()
    activity_calls = []
    batch_map = batch_map or {}

    runtime = WishlistManualDownloadRuntime(
        get_wishlist_service=lambda: wishlist_service,
        get_music_database=lambda: music_db,
        download_batches=batch_map,
        tasks_lock=_FakeLock(),
        missing_download_executor=executor,
        run_full_missing_tracks_process=lambda *args, **kwargs: None,
        get_batch_max_concurrent=lambda: 4,
        add_activity_item=lambda *args: activity_calls.append(args),
        active_server="navidrome",
        logger=logger,
        profile_id=1,
    )
    return runtime, wishlist_service, music_db, executor, logger, activity_calls, batch_map


def test_start_manual_wishlist_download_batch_filters_track_ids_and_starts_batch():
    runtime, _service, _db, executor, logger, activity_calls, batch_map = _build_runtime(
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
    assert activity_calls == [("", "Wishlist Download Started", "1 tracks", "Now")]
    assert len(executor.submissions) == 1
    _submitted_fn, submitted_args, _submitted_kwargs = executor.submissions[0]
    assert submitted_args[1] == "wishlist"
    assert submitted_args[2][0]["id"] == "track-2"
    assert submitted_args[2][0]["_original_index"] == 0
    assert batch_map[payload["batch_id"]]["analysis_total"] == 1
    assert batch_map[payload["batch_id"]]["force_download_all"] is True
    assert any("Filtered to 1 specific tracks by ID" in msg for msg in logger.info_messages)


def test_start_manual_wishlist_download_batch_skips_enhance_tracks_during_cleanup():
    runtime, service, _db, executor, logger, activity_calls, batch_map = _build_runtime(
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
    assert service.removed_ids == {"owned-1"}
    assert len(executor.submissions) == 1
    _submitted_fn, submitted_args, _submitted_kwargs = executor.submissions[0]
    assert [track["id"] for track in submitted_args[2]] == ["enhance-1"]
    assert batch_map[payload["batch_id"]]["analysis_total"] == 1
    assert activity_calls == [("", "Wishlist Download Started", "1 tracks", "Now")]
    assert any("Cleaned up 1 already-owned tracks" in msg for msg in logger.info_messages)
