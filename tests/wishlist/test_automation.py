from contextlib import contextmanager
from types import SimpleNamespace

from core.wishlist import processing
from core.wishlist.processing import WishlistAutoProcessingRuntime, process_wishlist_automatically


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


class _FakeProfilesDatabase:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_all_profiles(self):
        return list(self._profiles)


class _FakeWishlistService:
    def __init__(self, tracks, count=None):
        self._tracks = tracks
        self._count = count if count is not None else len(tracks)

    def get_wishlist_count(self, profile_id=1):
        return self._count

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self._tracks)

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
        return True


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self.calls = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last_sql = sql
        if "INSERT OR REPLACE INTO metadata" in sql and params:
            self.db.cycle_value = params[0]

    def fetchone(self):
        if "SELECT value FROM metadata WHERE key = 'wishlist_cycle'" in self._last_sql:
            return {"value": self.db.cycle_value}
        return None


class _FakeMusicDatabase:
    def __init__(self, cycle_value="albums"):
        self.cycle_value = cycle_value
        self.cursor_obj = _FakeCursor(self)
        self.commits = 0
        self.duplicate_removals = []
        self.track_checks = []

    def _get_connection(self):
        class _Conn:
            def __init__(self, outer):
                self.outer = outer

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return self.outer.cursor_obj

            def commit(self):
                self.outer.commits += 1

        return _Conn(self)

    def remove_wishlist_duplicates(self, profile_id=1):
        self.duplicate_removals.append(profile_id)
        return 0

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
        self.track_checks.append((track_name, artist_name, confidence_threshold, server_source, album))
        return None, 0.0


class _FakeExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, fn, *args, **kwargs):
        self.submissions.append((fn, args, kwargs))
        return SimpleNamespace()


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _build_runtime(
    *,
    tracks,
    cycle_value="albums",
    count=None,
    profiles=None,
    active_server="navidrome",
    progress_calls=None,
    guard_events=None,
    batch_map=None,
    guard_acquired=True,
    is_actually_processing=False,
):
    if progress_calls is None:
        progress_calls = []
    if guard_events is None:
        guard_events = []
    if batch_map is None:
        batch_map = {}

    wishlist_service = _FakeWishlistService(tracks, count=count)
    processing.get_wishlist_service = lambda: wishlist_service
    profiles_db = _FakeProfilesDatabase(profiles or [{"id": 1}])
    music_db = _FakeMusicDatabase(cycle_value=cycle_value)
    executor = _FakeExecutor()
    logger = _FakeLogger()

    @contextmanager
    def guard():
        guard_events.append("enter")
        try:
            yield guard_acquired
        finally:
            guard_events.append("exit")

    @contextmanager
    def app_context():
        yield

    def progress_callback(*args, **kwargs):
        progress_calls.append((args, kwargs))

    runtime = WishlistAutoProcessingRuntime(
        processing_guard=guard,
        app_context_factory=app_context,
        get_profiles_database=lambda: profiles_db,
        get_music_database=lambda: music_db,
        download_batches=batch_map,
        tasks_lock=_FakeLock(),
        update_automation_progress=progress_callback,
        automation_engine=None,
        missing_download_executor=executor,
        run_full_missing_tracks_process=lambda *args, **kwargs: None,
        get_batch_max_concurrent=lambda: 4,
        get_active_server=lambda: active_server,
        logger=logger,
        current_time_fn=lambda: 123.0,
        is_actually_processing=lambda: is_actually_processing,
        profile_id=1,
    )
    return runtime, wishlist_service, profiles_db, music_db, executor, logger, progress_calls, guard_events


def test_process_wishlist_automatically_toggles_cycle_when_no_tracks_match_current_cycle():
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[
            {
                "name": "Single Track",
                "artists": [{"name": "Artist A"}],
                "spotify_data": {"album": {"album_type": "single"}},
            }
        ],
        cycle_value="albums",
        count=1,
    )

    process_wishlist_automatically(runtime, automation_id="auto-1")

    assert executor.submissions == []
    assert music_db.cycle_value == "singles"
    assert music_db.commits == 1
    assert any("No albums tracks in wishlist" in msg for msg in logger.warning_messages)
    assert guard_events == ["enter", "exit"]
    assert [kwargs.get("progress") for _args, kwargs in progress_calls if "progress" in kwargs] == [10, 25, 40]


def test_process_wishlist_automatically_creates_batch_for_matching_tracks():
    batch_map = {}
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[
            {
                "name": "Album Track",
                "artists": [{"name": "Artist A"}],
                "spotify_data": {"album": {"album_type": "album"}},
            }
        ],
        cycle_value="albums",
        count=1,
        batch_map=batch_map,
    )

    process_wishlist_automatically(runtime, automation_id="auto-2")

    assert len(executor.submissions) == 1
    submitted_fn, submitted_args, submitted_kwargs = executor.submissions[0]
    assert submitted_args[1] == "wishlist"
    assert submitted_args[2][0]["_original_index"] == 0
    assert len(batch_map) == 1
    batch = next(iter(batch_map.values()))
    # ``queued`` is the initial state — the master worker flips it
    # to ``analysis`` as its first action when the executor picks
    # the batch up. Without this, wishlist runs with N > 3
    # sub-batches all rendered "Analyzing..." simultaneously even
    # though only 3 workers were running (UI lie).
    assert batch["phase"] == "queued"
    assert batch["playlist_name"] == "Wishlist (Auto - Albums)"
    assert batch["analysis_total"] == 1
    assert any(kwargs.get("progress") == 50 for _args, kwargs in progress_calls)
    assert guard_events == ["enter", "exit"]
    # Track has no album id/name → falls to residual batch path
    assert any("Starting wishlist residual batch" in msg for msg in logger.info_messages)


def test_wishlist_albums_cycle_splits_into_per_album_batches():
    """Multi-album wishlist run: each album with at least the
    threshold of missing tracks (default 2) emits its own sub-batch
    with ``is_album_download=True`` + populated album/artist context.
    Single-track-per-album items fall to residual and take the
    per-track path. Pinned so the album-bundle dispatch gate (which
    keys on those fields) engages per album instead of falling through
    to per-track on a single mixed batch."""
    batch_map = {}
    runtime, _service, _profiles_db, music_db, executor, _logger, _progress, _guards = _build_runtime(
        tracks=[
            # Album one: 2 missing tracks → promotes to album-bundle.
            {
                "name": "Song A1",
                "artists": [{"name": "Artist 1"}],
                "spotify_data": {
                    "album": {"id": "alb1", "name": "Album One", "album_type": "album"},
                    "artists": [{"name": "Artist 1"}],
                },
            },
            {
                "name": "Song A2",
                "artists": [{"name": "Artist 1"}],
                "spotify_data": {
                    "album": {"id": "alb1", "name": "Album One", "album_type": "album"},
                    "artists": [{"name": "Artist 1"}],
                },
            },
            # Album two: 3 missing tracks → also promotes.
            {
                "name": "Song B1",
                "artists": [{"name": "Artist 2"}],
                "spotify_data": {
                    "album": {"id": "alb2", "name": "Album Two", "album_type": "album"},
                    "artists": [{"name": "Artist 2"}],
                },
            },
            {
                "name": "Song B2",
                "artists": [{"name": "Artist 2"}],
                "spotify_data": {
                    "album": {"id": "alb2", "name": "Album Two", "album_type": "album"},
                    "artists": [{"name": "Artist 2"}],
                },
            },
            {
                "name": "Song B3",
                "artists": [{"name": "Artist 2"}],
                "spotify_data": {
                    "album": {"id": "alb2", "name": "Album Two", "album_type": "album"},
                    "artists": [{"name": "Artist 2"}],
                },
            },
        ],
        cycle_value="albums",
        count=5,
        batch_map=batch_map,
    )

    process_wishlist_automatically(runtime, automation_id="auto-multi-album")

    # Two album groups → two sub-batches submitted (no residual).
    assert len(executor.submissions) == 2
    assert len(batch_map) == 2

    # Each sub-batch must carry album-bundle dispatch context.
    for batch in batch_map.values():
        assert batch.get("is_album_download") is True
        assert batch.get("album_context", {}).get("name") in {"Album One", "Album Two"}
        assert batch.get("artist_context", {}).get("name") in {"Artist 1", "Artist 2"}

    submitted_track_lists = [submitted_args[2] for _fn, submitted_args, _kw in executor.submissions]
    track_counts = sorted(len(tracks) for tracks in submitted_track_lists)
    assert track_counts == [2, 3]

    # All sub-batches of one wishlist invocation share a single
    # ``wishlist_run_id`` so the completion handler can gate the
    # cycle toggle on "all siblings done".
    run_ids = {batch.get("wishlist_run_id") for batch in batch_map.values()}
    assert len(run_ids) == 1
    assert next(iter(run_ids))  # non-empty string


def test_wishlist_albums_cycle_residual_for_orphan_tracks():
    """Tracks without resolvable album metadata fall to the classic
    per-track residual batch (no ``is_album_download`` flag), while
    sibling tracks with valid album info AND enough missing tracks to
    clear the bundle threshold still get their own album-bundle
    sub-batch. With the default threshold bumped to 2, the album side
    needs at least two tracks from the same album to promote."""
    batch_map = {}
    runtime, _service, _profiles_db, music_db, executor, _logger, _progress, _guards = _build_runtime(
        tracks=[
            # Album side: two missing tracks from Album One → promotes.
            {
                "name": "Real Album Track 1",
                "artists": [{"name": "Artist 1"}],
                "spotify_data": {
                    "album": {"id": "alb1", "name": "Album One", "album_type": "album"},
                    "artists": [{"name": "Artist 1"}],
                },
            },
            {
                "name": "Real Album Track 2",
                "artists": [{"name": "Artist 1"}],
                "spotify_data": {
                    "album": {"id": "alb1", "name": "Album One", "album_type": "album"},
                    "artists": [{"name": "Artist 1"}],
                },
            },
            # Orphan: no album id, no album name → residual.
            {
                "name": "Orphan",
                "artists": [{"name": "X"}],
                "spotify_data": {"album": {"album_type": "album"}, "artists": [{"name": "X"}]},
            },
        ],
        cycle_value="albums",
        count=3,
        batch_map=batch_map,
    )

    process_wishlist_automatically(runtime, automation_id="auto-mixed")

    assert len(executor.submissions) == 2  # 1 album batch + 1 residual

    album_batches = [b for b in batch_map.values() if b.get("is_album_download")]
    residual_batches = [b for b in batch_map.values() if not b.get("is_album_download")]
    assert len(album_batches) == 1
    assert len(residual_batches) == 1
    assert album_batches[0]["album_context"]["name"] == "Album One"
    assert album_batches[0]["analysis_total"] == 2
    assert residual_batches[0]["analysis_total"] == 1


def test_process_wishlist_automatically_returns_early_when_already_processing():
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[
            {
                "name": "Album Track",
                "artists": [{"name": "Artist A"}],
                "spotify_data": {"album": {"album_type": "album"}},
            }
        ],
        cycle_value="albums",
        count=1,
        is_actually_processing=True,
    )

    process_wishlist_automatically(runtime, automation_id="auto-3")

    assert executor.submissions == []
    assert music_db.duplicate_removals == []
    assert progress_calls == []
    assert guard_events == []
    assert any("Already processing" in msg for msg in logger.info_messages)


def test_process_wishlist_automatically_returns_early_when_guard_not_acquired():
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[
            {
                "name": "Album Track",
                "artists": [{"name": "Artist A"}],
                "spotify_data": {"album": {"album_type": "album"}},
            }
        ],
        cycle_value="albums",
        count=1,
        guard_acquired=False,
    )

    process_wishlist_automatically(runtime, automation_id="auto-4")

    assert executor.submissions == []
    assert music_db.duplicate_removals == []
    assert progress_calls == []
    assert guard_events == ["enter", "exit"]
    assert any("race condition check" in msg for msg in logger.info_messages)


def test_process_wishlist_automatically_returns_early_when_no_tracks_are_available():
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[],
        cycle_value="albums",
        count=0,
    )

    process_wishlist_automatically(runtime, automation_id="auto-5")

    assert executor.submissions == []
    assert music_db.duplicate_removals == []
    assert guard_events == ["enter", "exit"]
    assert [kwargs.get("progress") for _args, kwargs in progress_calls if "progress" in kwargs] == [10]
    assert any("No tracks in wishlist for auto-processing" in msg for msg in logger.warning_messages)


def test_process_wishlist_automatically_skips_when_wishlist_batch_is_already_active():
    batch_map = {
        "batch-1": {
            "playlist_id": "wishlist",
            "phase": "analysis",
        }
    }
    runtime, _service, _profiles_db, music_db, executor, logger, progress_calls, guard_events = _build_runtime(
        tracks=[
            {
                "name": "Album Track",
                "artists": [{"name": "Artist A"}],
                "spotify_data": {"album": {"album_type": "album"}},
            }
        ],
        cycle_value="albums",
        count=1,
        batch_map=batch_map,
    )

    process_wishlist_automatically(runtime, automation_id="auto-6")

    assert executor.submissions == []
    assert music_db.duplicate_removals == []
    assert guard_events == ["enter", "exit"]
    assert [kwargs.get("progress") for _args, kwargs in progress_calls if "progress" in kwargs] == [10]
    assert any("already active in another batch" in msg for msg in logger.info_messages)
