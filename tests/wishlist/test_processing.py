from contextlib import contextmanager

from core.wishlist import processing


class _FakeLogger:
    def __init__(self):
        self.errors = []
        self.infos = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


class _FakeDB:
    def __init__(self):
        self.connection = _FakeConnection()

    @contextmanager
    def _get_connection(self):
        yield self.connection


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_remove_completed_tracks_from_wishlist_calls_remover():
    batch = {"queue": ["a", "b"]}
    download_tasks = {
        "a": {"status": "completed", "track_info": {"name": "Song A"}},
        "b": {"status": "failed", "track_info": {"name": "Song B"}},
    }
    calls = []

    removed = processing.remove_completed_tracks_from_wishlist(
        batch,
        download_tasks,
        lambda context: calls.append(context),
        logger=_FakeLogger(),
    )

    assert removed == 1
    assert calls == [{"track_info": {"name": "Song A"}, "original_search_result": {"name": "Song A"}}]


def test_add_cancelled_tracks_to_failed_tracks_builds_entries():
    batch = {"queue": ["a"], "cancelled_tracks": {1}}
    download_tasks = {
        "a": {
            "status": "cancelled",
            "track_index": 1,
            "track_info": {"name": "Song A", "artist": "Artist A", "artists": [{"name": "Artist A"}]},
            "cached_candidates": [{"title": "candidate"}],
        }
    }
    failed = []

    processed = processing.add_cancelled_tracks_to_failed_tracks(
        batch,
        download_tasks,
        failed,
        logger=_FakeLogger(),
    )

    assert processed == 1
    assert failed[0]["track_name"] == "Song A"
    assert failed[0]["artist_name"] == "Artist A"
    assert failed[0]["failure_reason"] == "Download cancelled"


def test_resolve_wishlist_source_type_for_batch_returns_album_for_album_batch():
    assert processing.resolve_wishlist_source_type_for_batch({"is_album_download": True}) == "album"


def test_resolve_wishlist_source_type_for_batch_returns_playlist_otherwise():
    assert processing.resolve_wishlist_source_type_for_batch({}) == "playlist"
    assert processing.resolve_wishlist_source_type_for_batch({"is_album_download": False}) == "playlist"


def test_build_wishlist_source_context_minimal_batch_skips_album_provenance():
    """Non-album batches must not carry album_context (would mislead the
    requeue logic into routing single-track sync failures through
    album_path)."""
    batch = {
        "playlist_name": "My Mix",
        "playlist_id": "pl-99",
    }

    context = processing.build_wishlist_source_context(batch)

    assert context["playlist_name"] == "My Mix"
    assert context["playlist_id"] == "pl-99"
    assert context["added_from"] == "webui_modal"
    assert "is_album_download" not in context
    assert "album_context" not in context
    assert "artist_context" not in context


def test_build_wishlist_source_context_preserves_album_context_for_album_batches():
    """Album batches must carry album_context/artist_context through to the
    wishlist row so a later requeue has authoritative routing data instead
    of having to reconstruct it from per-track album dicts."""
    batch = {
        "playlist_name": "Album: GNX",
        "playlist_id": "library_redownload_abc",
        "is_album_download": True,
        "album_context": {"id": "alb-1", "name": "GNX", "total_tracks": 12},
        "artist_context": {"id": "art-1", "name": "Kendrick Lamar"},
    }

    context = processing.build_wishlist_source_context(batch)

    assert context["is_album_download"] is True
    assert context["album_context"] == {"id": "alb-1", "name": "GNX", "total_tracks": 12}
    assert context["artist_context"] == {"id": "art-1", "name": "Kendrick Lamar"}


def test_recover_uncaptured_failed_tracks_builds_entries():
    batch = {"queue": ["a"]}
    download_tasks = {
        "a": {
            "status": "failed",
            "track_index": 2,
            "track_info": {"name": "Song B", "artist": "Artist B", "artists": [{"name": "Artist B"}]},
            "retry_count": 3,
            "error_message": "boom",
            "cached_candidates": [],
        }
    }
    failed = []

    recovered = processing.recover_uncaptured_failed_tracks(
        batch,
        download_tasks,
        failed,
        logger=_FakeLogger(),
    )

    assert recovered == 1
    assert failed[0]["track_name"] == "Song B"
    assert failed[0]["retry_count"] == 3
    assert failed[0]["failure_reason"] == "boom"


def test_finalize_auto_wishlist_completion_defers_toggle_when_siblings_active():
    """When the completing batch shares a ``wishlist_run_id`` with
    siblings still in pre-terminal phases, finalize must NOT toggle
    the cycle yet — that only happens when the LAST sibling done.
    Pinned to prevent the regression where every sub-batch's
    completion fired its own cycle toggle (Phase 1c.2.1 split path)."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 1, "total_failed": 1, "errors": 0}

    # Two sub-batches share the same run id. The first finishes,
    # the second is still 'analysis'.
    download_batches = {
        "batch-A": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-B": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "analysis"},
    }

    processing.finalize_auto_wishlist_completion(
        "batch-A",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    # Activity log still fires (it's a per-batch record), but cycle
    # toggle + state reset + automation emit are deferred.
    assert activities == [("", "Wishlist Updated", "1 failed tracks added to wishlist", "Now")]
    assert resets == []  # NOT reset yet — siblings still active
    assert automation_engine.events == []  # NOT emitted yet
    assert db.connection.cursor_obj.calls == []  # DB cycle-toggle NOT written


def test_finalize_auto_wishlist_completion_toggles_when_last_sibling_done():
    """When all siblings of the same run are in terminal phases (or
    don't exist), the completing batch IS the last → cycle toggles
    + state resets + automation event fires."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    summary = {"tracks_added": 1, "total_failed": 1, "errors": 0}

    download_batches = {
        # Both siblings already terminal — current batch is the last.
        "batch-A": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-B": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-C": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "analysis"},  # the completing one
    }

    processing.finalize_auto_wishlist_completion(
        "batch-C",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *_a: None,
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert resets == [True]
    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)
    assert automation_engine.events  # event emitted


def test_finalize_auto_wishlist_completion_legacy_no_run_id_toggles_immediately():
    """Back-compat: a batch with NO ``wishlist_run_id`` (legacy
    single-batch run from before Phase 1c.2.1) should keep firing
    the toggle on its own completion regardless of any unrelated
    batches in the dict."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    summary = {"tracks_added": 0, "total_failed": 0, "errors": 0}

    download_batches = {
        "batch-legacy": {"current_cycle": "albums"},  # no wishlist_run_id
        # Even with another unrelated batch active, legacy should toggle.
        "unrelated": {"current_cycle": "singles", "phase": "analysis"},
    }

    processing.finalize_auto_wishlist_completion(
        "batch-legacy",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *_a: None,
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert resets == [True]
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)


def test_finalize_auto_wishlist_completion_toggles_cycle_and_resets_state():
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 2, "total_failed": 5, "errors": 0}

    result = processing.finalize_auto_wishlist_completion(
        "batch-1",
        summary,
        download_batches={"batch-1": {"current_cycle": "albums"}},
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert result is summary
    assert resets == [True]
    assert activities == [("", "Wishlist Updated", "2 failed tracks added to wishlist", "Now")]
    assert automation_engine.events == [
        (
            "wishlist_processing_completed",
            {
                "tracks_processed": "5",
                "tracks_found": "2",
                "tracks_failed": "3",
            },
        )
    ]
    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)


def test_automatic_wishlist_cleanup_after_db_update_removes_library_matches():
    class _CleanupWishlistService:
        def __init__(self, tracks):
            self.tracks = tracks
            self.removed = []

        def get_wishlist_tracks_for_download(self, profile_id=1):
            return list(self.tracks)

        def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
            self.removed.append((spotify_track_id, success, error_message, profile_id))
            return True

    class _CleanupProfilesDatabase:
        def get_all_profiles(self):
            return [{"id": 1}]

    class _CleanupMusicDatabase:
        def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
            if track_name == "Song A" and artist_name == "Artist A":
                return {"id": "db-track"}, 0.9
            return None, 0.0

    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Song A",
                "artists": [{"name": "Artist A"}],
                "spotify_track_id": "sp-1",
                "id": "sp-1",
                "album": {"name": "Album A"},
            },
            {
                "name": "Song B",
                "artists": [{"name": "Artist B"}],
                "spotify_track_id": "sp-2",
                "id": "sp-2",
                "album": {"name": "Album B"},
            },
        ]
    )

    removed = processing.automatic_wishlist_cleanup_after_db_update(
        wishlist_service=wishlist_service,
        profiles_database=_CleanupProfilesDatabase(),
        music_database=_CleanupMusicDatabase(),
        active_server="navidrome",
        logger=_FakeLogger(),
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-1", True, None, 1)]


def test_automatic_wishlist_cleanup_after_db_update_removes_manual_matches(monkeypatch):
    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Manual Song",
                "artists": [{"name": "Artist A"}],
                "spotify_track_id": "sp-manual",
                "id": "sp-manual",
                "provider": "spotify",
                "album": {"name": "Album A"},
            },
        ]
    )
    music_db = _CleanupMusicDatabase()
    monkeypatch.setattr(
        "core.library.manual_library_match.get_match_for_track",
        lambda *_args, **_kwargs: {"id": 1, "library_track_id": 42},
    )

    removed = processing.automatic_wishlist_cleanup_after_db_update(
        wishlist_service=wishlist_service,
        profiles_database=_CleanupProfilesDatabase(),
        music_database=music_db,
        active_server="navidrome",
        logger=_FakeLogger(),
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-manual", True, None, 1)]
    assert music_db.track_checks == []


class _CleanupProfilesDatabase:
    def get_all_profiles(self):
        return [{"id": 1}]


class _CleanupWishlistService:
    def __init__(self, tracks):
        self._tracks = list(tracks)
        self.removed = []

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self._tracks)

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
        self.removed.append((spotify_track_id, success, error_message, profile_id))
        return True


class _CleanupMusicDatabase:
    def __init__(self):
        self.track_checks = []

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
        self.track_checks.append((track_name, artist_name, server_source, album))
        if track_name == "Owned Song" and artist_name == "Artist A":
            return {"id": "db-track"}, 0.9
        if track_name == "Broken Song":
            raise RuntimeError("boom")
        return None, 0.0


def test_remove_tracks_already_in_library_skips_enhance_tracks_and_handles_errors():
    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Owned Song",
                "artists": ["Artist A"],
                "spotify_track_id": "sp-1",
                "album": {"name": "Album A"},
            },
            {
                "name": "Enhance Song",
                "artists": [{"name": "Artist B"}],
                "spotify_track_id": "sp-2",
                "source_type": "enhance",
                "album": {"name": "Album B"},
            },
            {
                "name": "Broken Song",
                "artists": [{"name": "Artist C"}],
                "spotify_track_id": "sp-3",
                "album": {"name": "Album C"},
            },
        ]
    )
    music_db = _CleanupMusicDatabase()

    removed = processing.remove_tracks_already_in_library(
        wishlist_service,
        _CleanupProfilesDatabase(),
        music_db,
        "navidrome",
        logger=_FakeLogger(),
        skip_track_fn=lambda track: track.get("source_type") == "enhance",
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-1", True, None, 1)]
    assert music_db.track_checks == [
        ("Owned Song", "Artist A", "navidrome", "Album A"),
        ("Broken Song", "Artist C", "navidrome", "Album C"),
    ]


def test_finalize_auto_wishlist_completion_with_no_tracks_added_still_resets_state():
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 0, "total_failed": 5, "errors": 0}

    result = processing.finalize_auto_wishlist_completion(
        "batch-2",
        summary,
        download_batches={"batch-2": {"current_cycle": "singles"}},
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert result is summary
    assert resets == [True]
    assert activities == []
    assert automation_engine.events == [
        (
            "wishlist_processing_completed",
            {
                "tracks_processed": "5",
                "tracks_found": "0",
                "tracks_failed": "5",
            },
        )
    ]
    assert db.connection.committed is True

