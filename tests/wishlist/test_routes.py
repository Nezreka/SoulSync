import json

from core.wishlist.routes import (
    WishlistRouteRuntime,
    add_album_track_to_wishlist,
    clear_wishlist,
    get_wishlist_count,
    get_wishlist_cycle,
    get_wishlist_stats,
    get_wishlist_tracks,
    process_wishlist_api,
    remove_album_from_wishlist,
    remove_batch_from_wishlist,
    remove_track_from_wishlist,
    set_wishlist_cycle,
)


class _FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []
        self.debug_messages = []

    def info(self, msg, *args):
        self.info_messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.warning_messages.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.error_messages.append(msg % args if args else msg)

    def debug(self, msg, *args):
        self.debug_messages.append(msg % args if args else msg)


class _FakeThread:
    def __init__(self, target=None, daemon=False):
        self.target = target
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True
        if self.target:
            self.target()


class _FakeThreadFactory:
    def __init__(self):
        self.created = []

    def __call__(self, *args, **kwargs):
        thread = _FakeThread(*args, **kwargs)
        self.created.append(thread)
        return thread


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = sql
        if "INSERT OR REPLACE INTO metadata" in sql and params:
            self.db.cycle_value = params[0]

    def fetchone(self):
        if "SELECT value FROM metadata WHERE key = 'wishlist_cycle'" in self.last_sql:
            return {"value": self.db.cycle_value}
        return None


class _FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.db.cursor_obj

    def commit(self):
        self.db.commits += 1


class _FakeMusicDatabase:
    def __init__(self, cycle_value="albums", duplicate_removals=0):
        self.cycle_value = cycle_value
        self.duplicate_removals = duplicate_removals
        self.commits = 0
        self.cursor_obj = _FakeCursor(self)
        self.duplicate_cleanup_profiles = []

    def _get_connection(self):
        return _FakeConnection(self)

    def remove_wishlist_duplicates(self, profile_id=1):
        self.duplicate_cleanup_profiles.append(profile_id)
        return self.duplicate_removals


class _FakeWishlistService:
    def __init__(self, tracks=None, count=None, clear_result=True):
        self.tracks = list(tracks or [])
        self.count = len(self.tracks) if count is None else count
        self.clear_result = clear_result
        self.removed = []
        self.add_calls = []

    def get_wishlist_count(self, profile_id=1):
        return self.count

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self.tracks)

    def clear_wishlist(self, profile_id=1):
        return self.clear_result

    def remove_track_from_wishlist(self, spotify_track_id, profile_id=1):
        self.removed.append((spotify_track_id, profile_id))
        return True

    def add_spotify_track_to_wishlist(self, **kwargs):
        self.add_calls.append(kwargs)
        return True


def _build_runtime(
    *,
    tracks=None,
    count=None,
    cycle_value="albums",
    duplicate_removals=0,
    clear_result=True,
    auto_processing_flag=False,
    actually_processing=False,
    next_run_seconds=0,
    download_batches=None,
    download_tasks=None,
    thread_factory=None,
    reset_callback=None,
):
    service = _FakeWishlistService(tracks=tracks, count=count, clear_result=clear_result)
    db = _FakeMusicDatabase(cycle_value=cycle_value, duplicate_removals=duplicate_removals)
    logger = _FakeLogger()
    activity_calls = []
    runtime = WishlistRouteRuntime(
        get_wishlist_service=lambda: service,
        get_music_database=lambda: db,
        get_current_profile_id=lambda: 1,
        download_batches=download_batches if download_batches is not None else {},
        download_tasks=download_tasks if download_tasks is not None else {},
        tasks_lock=_FakeLock(),
        is_wishlist_auto_processing_flag=lambda: auto_processing_flag,
        is_wishlist_actually_processing=lambda: actually_processing,
        reset_wishlist_processing_state=reset_callback or (lambda: None),
        add_activity_item=lambda *args: activity_calls.append(args),
        logger=logger,
        active_server="navidrome",
        get_next_run_seconds=(lambda _name: next_run_seconds),
        thread_factory=thread_factory or _FakeThreadFactory(),
    )
    return runtime, service, db, logger, activity_calls


def test_process_wishlist_api_starts_background_thread_when_idle():
    thread_factory = _FakeThreadFactory()
    runtime, _service, _db, logger, _activity_calls = _build_runtime(
        thread_factory=thread_factory,
    )
    start_calls = []

    payload, status = process_wishlist_api(runtime, start_processing=lambda: start_calls.append("ran"))

    assert status == 200
    assert payload == {"success": True, "message": "Wishlist processing started"}
    assert start_calls == ["ran"]
    assert len(thread_factory.created) == 1
    assert thread_factory.created[0].daemon is True
    assert thread_factory.created[0].started is True
    assert logger.error_messages == []


def test_process_wishlist_api_rejects_when_flag_is_set():
    thread_factory = _FakeThreadFactory()
    runtime, _service, _db, logger, _activity_calls = _build_runtime(
        auto_processing_flag=True,
        thread_factory=thread_factory,
    )

    payload, status = process_wishlist_api(runtime, start_processing=lambda: None)

    assert status == 409
    assert payload == {"success": False, "error": "Wishlist processing already in progress"}
    assert thread_factory.created == []
    assert logger.error_messages == []


def test_get_wishlist_count_returns_profile_count():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime(count=7)

    payload, status = get_wishlist_count(runtime)

    assert status == 200
    assert payload == {"count": 7}


def test_get_wishlist_stats_uses_cycle_and_next_run():
    tracks = [
        {
            "id": "track-1",
            "name": "Single Song",
            "artists": [{"name": "Artist One"}],
            "spotify_data": {"album": {"album_type": "single"}},
        },
        {
            "id": "track-2",
            "name": "Album Song",
            "artists": [{"name": "Artist Two"}],
            "spotify_data": {"album": {"total_tracks": 8}},
        },
    ]
    runtime, _service, _db, _logger, _activity_calls = _build_runtime(
        tracks=tracks,
        count=2,
        cycle_value="albums",
        actually_processing=True,
        next_run_seconds=123,
    )

    payload, status = get_wishlist_stats(runtime)

    assert status == 200
    assert payload == {
        "singles": 1,
        "albums": 1,
        "total": 2,
        "next_run_in_seconds": 123,
        "is_auto_processing": True,
        "current_cycle": "albums",
    }


def test_get_wishlist_tracks_filters_category_and_cleans_duplicates():
    tracks = [
        {
            "id": "track-1",
            "name": "Single One",
            "artists": [{"name": "Artist One"}],
            "spotify_data": {"album": {"album_type": "single"}},
        },
        {
            "id": "track-2",
            "name": "Single Two",
            "artists": [{"name": "Artist Two"}],
            "spotify_data": {"album": {"album_type": "single"}},
        },
        {
            "id": "track-3",
            "name": "Album Song",
            "artists": [{"name": "Artist Three"}],
            "spotify_data": {"album": {"total_tracks": 8}},
        },
    ]
    runtime, service, db, logger, _activity_calls = _build_runtime(
        tracks=tracks,
        duplicate_removals=2,
    )

    payload, status = get_wishlist_tracks(runtime, category="singles", limit=1)

    assert status == 200
    assert payload["category"] == "singles"
    assert payload["total"] == 2
    assert len(payload["tracks"]) == 1
    assert payload["tracks"][0]["id"] == "track-1"
    assert db.duplicate_cleanup_profiles == [1]
    assert any("duplicate tracks from wishlist" in msg for msg in logger.warning_messages)
    assert service.get_wishlist_tracks_for_download(profile_id=1)[0]["id"] == "track-1"


def test_clear_wishlist_cancels_active_batches_and_resets_state():
    download_batches = {
        "batch-1": {
            "playlist_id": "wishlist",
            "phase": "analysis",
            "queue": ["task-1", "task-2", "task-3"],
        },
        "batch-2": {
            "playlist_id": "other",
            "phase": "analysis",
            "queue": ["task-4"],
        },
    }
    download_tasks = {
        "task-1": {"status": "queued"},
        "task-2": {"status": "in_progress"},
        "task-3": {"status": "completed"},
        "task-4": {"status": "queued"},
    }
    reset_calls = []
    runtime, service, _db, logger, activity_calls = _build_runtime(
        download_batches=download_batches,
        download_tasks=download_tasks,
        reset_callback=lambda: reset_calls.append("reset"),
    )

    payload, status = clear_wishlist(runtime)

    assert status == 200
    assert payload == {
        "success": True,
        "message": "Wishlist cleared successfully",
        "cancelled_downloads": 2,
    }
    assert service.clear_result is True
    assert download_batches["batch-1"]["phase"] == "cancelled"
    assert download_batches["batch-2"]["phase"] == "analysis"
    assert download_tasks["task-1"]["status"] == "cancelled"
    assert download_tasks["task-2"]["status"] == "cancelled"
    assert download_tasks["task-3"]["status"] == "completed"
    assert download_tasks["task-4"]["status"] == "queued"
    assert reset_calls == ["reset"]
    assert activity_calls == [
        ("", "Wishlist Cleared", "Wishlist cleared and 2 downloads cancelled", "Now")
    ]
    assert any("Cancelled 2 active wishlist downloads" in msg for msg in logger.warning_messages)


def test_remove_track_from_wishlist_requires_track_id():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = remove_track_from_wishlist(runtime, None)

    assert status == 400
    assert payload == {"success": False, "error": "No spotify_track_id provided"}


def test_remove_track_from_wishlist_removes_single_track():
    runtime, service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = remove_track_from_wishlist(runtime, "track-1")

    assert status == 200
    assert payload == {"success": True, "message": "Track removed from wishlist"}
    assert service.removed == [("track-1", 1)]


def test_remove_album_from_wishlist_matches_album_name():
    tracks = [
        {
            "wishlist_id": 1,
            "spotify_track_id": "track-1",
            "id": "track-1",
            "spotify_data": json.dumps(
                {
                    "album": {"name": "Complete Album"},
                    "artists": [{"name": "Artist One"}],
                }
            ),
        },
        {
            "wishlist_id": 2,
            "spotify_track_id": "track-2",
            "id": "track-2",
            "spotify_data": {
                "album": {"name": "Other Album"},
                "artists": [{"name": "Artist Two"}],
            },
        },
    ]
    runtime, service, _db, _logger, _activity_calls = _build_runtime(tracks=tracks)

    payload, status = remove_album_from_wishlist(runtime, album_name_filter="complete album")

    assert status == 200
    assert payload == {
        "success": True,
        "message": "Removed 1 track(s) from wishlist",
        "removed_count": 1,
    }
    assert service.removed == [("track-1", 1)]


def test_remove_batch_from_wishlist_returns_removed_count():
    runtime, service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = remove_batch_from_wishlist(runtime, ["track-1", "track-2"])

    assert status == 200
    assert payload == {
        "success": True,
        "removed": 2,
        "message": "Removed 2 tracks from wishlist",
    }
    assert service.removed == [("track-1", 1), ("track-2", 1)]


def test_set_wishlist_cycle_updates_metadata():
    runtime, _service, db, _logger, _activity_calls = _build_runtime(cycle_value="albums")

    payload, status = set_wishlist_cycle(runtime, "singles")

    assert status == 200
    assert payload == {"success": True, "cycle": "singles"}
    assert db.cycle_value == "singles"
    assert db.commits == 1


def test_get_wishlist_cycle_returns_stored_value():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime(cycle_value="singles")

    payload, status = get_wishlist_cycle(runtime)

    assert status == 200
    assert payload == {"cycle": "singles"}


def test_add_album_track_to_wishlist_builds_spotify_payload_and_merges_context():
    runtime, service, _db, _logger, _activity_calls = _build_runtime()
    track = {
        "id": "track-1",
        "name": "Song One",
        "artists": [{"name": "Artist One"}],
        "duration_ms": 1234,
        "track_number": 2,
        "disc_number": 1,
        "explicit": True,
        "popularity": 77,
        "preview_url": "https://example.test/preview",
        "external_urls": {"spotify": "https://open.spotify.com/track/track-1"},
    }
    artist = {"id": "artist-1", "name": "Artist One"}
    album = {
        "id": "album-1",
        "name": "Album One",
        "artists": [{"name": "Artist One"}],
        "image_url": "https://example.test/cover.jpg",
        "release_date": "2024-01-01",
        "total_tracks": 10,
    }

    payload, status = add_album_track_to_wishlist(
        runtime,
        track=track,
        artist=artist,
        album=album,
        source_type="album",
        source_context={"playlist_id": "pl-1"},
    )

    assert status == 200
    assert payload == {"success": True, "message": "Added 'Song One' to wishlist"}
    assert len(service.add_calls) == 1
    add_call = service.add_calls[0]
    assert add_call["failure_reason"] == "Added from library (incomplete album)"
    assert add_call["source_type"] == "album"
    assert add_call["profile_id"] == 1
    assert add_call["source_context"] == {
        "playlist_id": "pl-1",
        "artist_id": "artist-1",
        "artist_name": "Artist One",
        "album_id": "album-1",
        "album_name": "Album One",
        "added_via": "library_wishlist_modal",
    }
    assert add_call["spotify_track_data"]["album"]["images"] == [
        {"url": "https://example.test/cover.jpg", "height": 640, "width": 640}
    ]
    assert add_call["spotify_track_data"]["duration_ms"] == 1234
    assert add_call["spotify_track_data"]["explicit"] is True
