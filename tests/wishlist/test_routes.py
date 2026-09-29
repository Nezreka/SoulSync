import json
from types import SimpleNamespace

import core.wishlist.routes as routes_module
from core.wishlist.routes import (
    BULK_WISHLIST_MAX_IDS,
    WishlistRouteRuntime,
    add_album_track_to_wishlist,
    bulk_wishlist_action,
    clear_wishlist,
    get_retry_profile,
    get_wishlist_count,
    get_wishlist_cycle,
    get_wishlist_stats,
    get_wishlist_tracks,
    process_wishlist_api,
    remove_album_from_wishlist,
    remove_batch_from_wishlist,
    remove_track_from_wishlist,
    set_wishlist_cycle,
    set_retry_profile,
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
        self.ignore_writes = []
        self.retry_resets = []
        self.wishlist_rows = {}
        self.metadata = {}

    def get_metadata(self, key, default=None):
        return self.metadata.get(key, default)

    def set_metadata(self, key, value):
        self.metadata[key] = value

    def _get_connection(self):
        return _FakeConnection(self)

    def remove_wishlist_duplicates(self, profile_id=1):
        self.duplicate_cleanup_profiles.append(profile_id)
        return self.duplicate_removals

    def add_to_wishlist_ignore(self, track_id, track_name="", artist_name="",
                               reason="", profile_id=1):
        self.ignore_writes.append({
            "track_id": track_id, "track_name": track_name,
            "artist_name": artist_name, "reason": reason,
            "profile_id": profile_id,
        })
        return True

    def get_wishlist_spotify_data(self, track_id, profile_id=1):
        return {"name": track_id, "artists": [{"name": "Fake Artist"}]}

    def get_wishlist_track(self, track_id, profile_id=1):
        return self.wishlist_rows.get(track_id)

    def reset_wishlist_retry_backoff(self, spotify_track_ids=None, profile_id=None):
        ids = [str(t) for t in (spotify_track_ids or [])]
        self.retry_resets.append((ids, profile_id))
        return sum(1 for tid in ids
                   if (self.wishlist_rows.get(tid, {}).get("retry_count") or 0) > 0)


class _FakeWishlistService:
    def __init__(self, tracks=None, count=None, clear_result=True):
        self.tracks = list(tracks or [])
        self.count = len(self.tracks) if count is None else count
        self.clear_result = clear_result
        self.removed = []
        self.add_calls = []
        self.remove_fail_ids = set()

    def get_wishlist_count(self, profile_id=1):
        return self.count

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self.tracks)

    def clear_wishlist(self, profile_id=1):
        return self.clear_result

    def remove_track_from_wishlist(self, spotify_track_id, profile_id=1):
        if spotify_track_id in self.remove_fail_ids:
            return False
        self.removed.append((spotify_track_id, profile_id))
        return True

    def add_track_to_wishlist(self, **kwargs):
        self.add_calls.append(kwargs)
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
    actually_processing=False,
    next_run_seconds=0,
    download_batches=None,
    download_tasks=None,
    thread_factory=None,
    reset_callback=None,
):
    service = _FakeWishlistService(tracks=tracks, count=count, clear_result=clear_result)
    routes_module.get_wishlist_service = lambda: service
    db = _FakeMusicDatabase(cycle_value=cycle_value, duplicate_removals=duplicate_removals)
    service.database = db
    logger = _FakeLogger()
    activity_calls = []
    runtime = WishlistRouteRuntime(
        get_music_database=lambda: db,
        profile_id=1,
        download_batches=download_batches if download_batches is not None else {},
        download_tasks=download_tasks if download_tasks is not None else {},
        tasks_lock=_FakeLock(),
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
        actually_processing=True,
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
        download_batches={
            "active": {"playlist_id": "wishlist", "phase": "downloading"},
            "done": {"playlist_id": "wishlist", "phase": "complete"},
            "other": {"playlist_id": "spotify", "phase": "downloading"},
        },
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
        "active_batches": 1,
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
    assert add_call["track_data"]["album"]["images"] == [
        {"url": "https://example.test/cover.jpg", "height": 640, "width": 640}
    ]
    assert add_call["track_data"]["duration_ms"] == 1234
    assert add_call["track_data"]["explicit"] is True


def test_set_wishlist_cycle_rejects_invalid_cycle():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = set_wishlist_cycle(runtime, "mixes")

    assert status == 400
    assert payload == {"error": "Invalid cycle. Must be 'albums' or 'singles'"}


def test_remove_album_from_wishlist_matches_album_id():
    tracks = [
        {
            "wishlist_id": 1,
            "spotify_track_id": "track-1",
            "id": "track-1",
            "spotify_data": {
                "album": {"id": "album-1", "name": "Complete Album"},
                "artists": [{"name": "Artist One"}],
            },
        },
        {
            "wishlist_id": 2,
            "spotify_track_id": "track-2",
            "id": "track-2",
            "spotify_data": {
                "album": {"id": "album-2", "name": "Other Album"},
                "artists": [{"name": "Artist Two"}],
            },
        },
    ]
    runtime, service, _db, _logger, _activity_calls = _build_runtime(tracks=tracks)

    payload, status = remove_album_from_wishlist(runtime, album_id="album-1")

    assert status == 200
    assert payload == {
        "success": True,
        "message": "Removed 1 track(s) from wishlist",
        "removed_count": 1,
    }
    assert service.removed == [("track-1", 1)]


def test_remove_album_from_wishlist_requires_lookup_fields():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = remove_album_from_wishlist(runtime)

    assert status == 400
    assert payload == {"success": False, "error": "No album_id or album_name provided"}


def test_remove_batch_from_wishlist_rejects_invalid_payload():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = remove_batch_from_wishlist(runtime, "track-1")

    assert status == 400
    assert payload == {"success": False, "error": "Missing or invalid spotify_track_ids"}


def test_add_album_track_to_wishlist_requires_required_fields():
    runtime, _service, _db, _logger, _activity_calls = _build_runtime()

    payload, status = add_album_track_to_wishlist(runtime, track=None, artist=None, album=None)

    assert status == 400
    assert payload == {
        "success": False,
        "error": "Missing required fields: track, artist, album",
    }


# ── #825: don't add already-owned tracks to the wishlist (respects the toggle) ──

def _own_track_args():
    return dict(track={"id": "t", "name": "Song", "artists": [{"name": "A"}]},
                artist={"id": "a1", "name": "A"},
                album={"id": "al1", "name": "Album"}, source_type="album")


def test_add_album_track_skips_owned_when_duplicates_off(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: False if key == 'wishlist.allow_duplicate_tracks' else default)
    runtime, service, db, _logger, _ = _build_runtime()
    owned = SimpleNamespace(title='Song', album_title='Album', artist_name='A',
                            file_path='/music/Song.flac')
    db.check_track_exists = lambda *a, **k: (owned, 0.95)

    payload, status = add_album_track_to_wishlist(runtime, **_own_track_args())

    assert status == 200
    assert payload.get("skipped") is True
    assert service.add_calls == []                             # nothing added


def test_add_album_track_keeps_request_when_fuzzy_hit_is_another_song(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: False if key == 'wishlist.allow_duplicate_tracks' else default)
    runtime, service, db, _logger, _ = _build_runtime()
    similar = SimpleNamespace(title='Songs', album_title='Album', artist_name='A',
                              file_path='/music/Songs.flac')
    db.check_track_exists = lambda *a, **k: (similar, 0.95)

    payload, status = add_album_track_to_wishlist(runtime, **_own_track_args())

    assert status == 200
    assert not payload.get('skipped')
    assert len(service.add_calls) == 1


def test_add_album_track_adds_missing_when_duplicates_off(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: False if key == 'wishlist.allow_duplicate_tracks' else default)
    runtime, service, db, _logger, _ = _build_runtime()
    db.check_track_exists = lambda *a, **k: (None, 0.0)        # not in library

    payload, status = add_album_track_to_wishlist(runtime, **_own_track_args())

    assert status == 200
    assert not payload.get("skipped")
    assert len(service.add_calls) == 1                         # added


def test_add_album_track_adds_owned_when_duplicates_on(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: True if key == 'wishlist.allow_duplicate_tracks' else default)
    runtime, service, db, _logger, _ = _build_runtime()
    called = []
    db.check_track_exists = lambda *a, **k: called.append(1) or (object(), 0.99)

    payload, status = add_album_track_to_wishlist(runtime, **_own_track_args())

    assert status == 200
    assert len(service.add_calls) == 1                         # added anyway (user wants dupes)
    assert called == []                                        # ownership check skipped entirely


# ── per-artist removal (#1065) ───────────────────────────────────────────────

def _artist_tracks():
    return [
        {"wishlist_id": 1, "spotify_track_id": "qt-1", "id": "qt-1",
         "spotify_data": json.dumps({"album": {"name": "A1"},
                                     "artists": [{"name": "Big Discography"}]})},
        {"wishlist_id": 2, "spotify_track_id": "qt-2", "id": "qt-2",
         "spotify_data": {"album": {"name": "A2"},
                          "artists": [{"name": "big discography"}]}},   # case differs
        {"wishlist_id": 3, "spotify_track_id": "keep-1", "id": "keep-1",
         "spotify_data": {"album": {"name": "B1"},
                          "artists": [{"name": "Keep Me"}]}},
        {"wishlist_id": 4, "spotify_track_id": "feat-1", "id": "feat-1",
         # Big Discography only FEATURES here — primary artist is someone else,
         # so a per-artist purge must NOT take it
         "spotify_data": {"album": {"name": "C1"},
                          "artists": [{"name": "Keep Me"}, {"name": "Big Discography"}]}},
    ]


def test_remove_artist_takes_whole_catalog_case_insensitively():
    from core.wishlist.routes import remove_artist_from_wishlist
    runtime, service, _db, _logger, _a = _build_runtime(tracks=_artist_tracks())

    payload, status = remove_artist_from_wishlist(runtime, artist_name="  Big Discography ")

    assert status == 200
    assert payload["removed_count"] == 2
    assert [t for t, _p in service.removed] == ["qt-1", "qt-2"]   # feature + others kept


def test_remove_artist_unknown_404_and_missing_400():
    from core.wishlist.routes import remove_artist_from_wishlist
    runtime, service, _db, _logger, _a = _build_runtime(tracks=_artist_tracks())
    payload, status = remove_artist_from_wishlist(runtime, artist_name="Nobody Here")
    assert status == 404 and service.removed == []
    payload, status = remove_artist_from_wishlist(runtime, artist_name="   ")
    assert status == 400


# ── bulk actions (grab / skip / retry) ───────────────────────────────────────

def test_bulk_action_rejects_unknown_action_and_bad_ids():
    runtime, _service, _db, _logger, _a = _build_runtime()
    payload, status = bulk_wishlist_action(runtime, "nuke", ["t1"])
    assert status == 400 and payload["success"] is False
    payload, status = bulk_wishlist_action(runtime, "grab", [])
    assert status == 400 and payload["success"] is False
    payload, status = bulk_wishlist_action(
        runtime, "skip", [f"t{i}" for i in range(BULK_WISHLIST_MAX_IDS + 1)])
    assert status == 400
    assert "max 200" in payload["error"]


def test_bulk_skip_removes_and_records_skipped_ignore():
    runtime, service, db, _logger, _a = _build_runtime()
    service.remove_fail_ids = {"gone"}

    payload, status = bulk_wishlist_action(runtime, "skip", ["t1", "gone", "t2"])

    assert status == 207                                  # one item failed alone
    assert payload["success"] is False
    assert [t for t, _p in service.removed] == ["t1", "t2"]
    assert [w["reason"] for w in db.ignore_writes] == ["skipped", "skipped"]
    by_id = {r["id"]: (r["ok"], r["message"]) for r in payload["results"]}
    assert by_id["t1"] == (True, "Skipped")
    assert by_id["t2"] == (True, "Skipped")
    assert by_id["gone"] == (False, "Not in wishlist")


def test_bulk_retry_clears_backoff_only_on_failing_tracks():
    runtime, _service, db, _logger, _a = _build_runtime()
    db.wishlist_rows = {
        "t1": {"retry_count": 3, "last_attempted": "2026-09-26 10:00:00"},
        "t2": {"retry_count": 0, "last_attempted": None},
    }

    payload, status = bulk_wishlist_action(runtime, "retry", ["t1", "t2", "missing"])

    assert status == 207
    assert db.retry_resets == [(["t1"], 1)]               # only the failing row
    by_id = {r["id"]: (r["ok"], r["message"]) for r in payload["results"]}
    assert by_id["t1"] == (True, "Retry clock cleared")
    assert by_id["t2"] == (False, "No failed attempts to clear")
    assert by_id["missing"] == (False, "Not in wishlist")


def test_bulk_retry_all_clean_reports_noop():
    runtime, _service, db, _logger, _a = _build_runtime()
    db.wishlist_rows = {"t2": {"retry_count": 0, "last_attempted": None}}

    payload, status = bulk_wishlist_action(runtime, "retry", ["t2"])

    assert status == 207
    assert db.retry_resets == []                          # DB helper never called
    assert payload["results"] == [
        {"id": "t2", "ok": False, "message": "No failed attempts to clear"}]


def test_bulk_grab_queues_batch_for_selected_ids():
    runtime, _service, _db, _logger, _a = _build_runtime()
    calls = []

    def fake_start(ids):
        calls.append(list(ids))
        return {"success": True, "batch_id": "batch-9"}, 200

    payload, status = bulk_wishlist_action(
        runtime, "grab", ["t2", "t1", "t2"], start_batch=fake_start)

    assert status == 200
    assert calls == [["t2", "t1"]]                        # deduped, order kept
    assert payload["batch_id"] == "batch-9"
    assert payload["results"] == [
        {"id": "t2", "ok": True, "message": "Queued for download"},
        {"id": "t1", "ok": True, "message": "Queued for download"},
    ]


def test_bulk_grab_without_runner_is_500():
    runtime, _service, _db, _logger, _a = _build_runtime()

    payload, status = bulk_wishlist_action(runtime, "grab", ["t1"])

    assert status == 500
    assert payload["success"] is False


def test_get_retry_profile_defaults_to_standard():
    runtime, _service, _db, _logger, _a = _build_runtime()

    payload, status = get_retry_profile(runtime)

    assert status == 200
    assert payload["success"] is True
    assert payload["profile"]["name"] == "standard"
    assert {p["name"] for p in payload["profiles"]} == {
        "standard", "aggressive", "patient"}             # custom is API-only


def test_set_retry_profile_switches_and_persists():
    runtime, _service, _db, _logger, _a = _build_runtime()

    payload, status = set_retry_profile(runtime, {"profile": "patient"})

    assert status == 200
    assert payload["profile"]["name"] == "patient"
    payload, status = get_retry_profile(runtime)
    assert status == 200
    assert payload["profile"]["name"] == "patient"


def test_set_retry_profile_rejects_garbage_with_400():
    runtime, _service, _db, _logger, _a = _build_runtime()

    payload, status = set_retry_profile(runtime, {"profile": "turbo"})

    assert status == 400
    assert "error" in payload
    # the active profile is unchanged
    payload, _ = get_retry_profile(runtime)
    assert payload["profile"]["name"] == "standard"


def test_set_retry_profile_accepts_a_custom_ladder():
    runtime, _service, _db, _logger, _a = _build_runtime()

    payload, status = set_retry_profile(runtime, {
        "profile": "custom", "ladder": {"2": 600, "3": 3600}, "max_cooldown": 7200})

    assert status == 200
    assert payload["profile"]["name"] == "custom"
    assert payload["profile"]["ladder"] == {"2": 600, "3": 3600}
