import os
from concurrent.futures import Future

import core.imports.routes as import_routes
from core.imports.routes import (
    ImportRouteRuntime,
    album_match,
    album_process,
    process_single_import_file,
    search_albums,
    search_tracks,
    singles_process,
    staging_files,
    staging_groups,
    staging_hints,
    staging_suggestions,
)


class _FakeLogger:
    def __init__(self):
        self.debug_messages = []
        self.error_messages = []
        self.info_messages = []
        self.warning_messages = []

    def debug(self, msg, *args):
        self.debug_messages.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.error_messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.info_messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.warning_messages.append(msg % args if args else msg)


class _FakeHydrabaseWorker:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, query, kind):
        self.enqueued.append((query, kind))


class _FakeAutomationEngine:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def emit(self, name, payload):
        if self.fail:
            raise RuntimeError("emit boom")
        self.events.append((name, payload))


class _FakeExecutor:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        future = Future()
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                future.set_exception(outcome)
            else:
                future.set_result(outcome)
        else:
            future.set_result(fn(*args))
        return future


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _metadata_for(files):
    def _read_metadata(file_path, rel_path):
        return files[rel_path]

    return _read_metadata


def test_staging_files_returns_audio_files_with_metadata(tmp_path):
    _touch(tmp_path / "Artist" / "02 - Song.flac")
    _touch(tmp_path / "cover.jpg")
    rel_song = os.path.join("Artist", "02 - Song.flac")
    metadata = {
        rel_song: {
            "title": "Song",
            "artist": "Track Artist",
            "albumartist": "Album Artist",
            "album": "Album",
            "track_number": 2,
            "disc_number": 1,
        }
    }
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path),
        read_staging_file_metadata=_metadata_for(metadata),
        logger=_FakeLogger(),
    )

    payload, status = staging_files(runtime)

    assert status == 200
    assert payload["success"] is True
    assert payload["staging_path"] == str(tmp_path)
    assert len(payload["files"]) == 1
    assert payload["files"] == [
        {
            "filename": "02 - Song.flac",
            "rel_path": rel_song,
            "full_path": str(tmp_path / "Artist" / "02 - Song.flac"),
            "title": "Song",
            "artist": "Album Artist",
            "album": "Album",
            "track_number": 2,
            "disc_number": 1,
            "extension": ".flac",
        }
    ]


def test_staging_groups_only_returns_multi_file_album_groups(tmp_path):
    _touch(tmp_path / "a.mp3")
    _touch(tmp_path / "b.mp3")
    _touch(tmp_path / "single.mp3")
    metadata = {
        "a.mp3": {
            "title": "A",
            "artist": "Artist",
            "albumartist": "",
            "album": "Album",
            "track_number": 2,
            "disc_number": 1,
        },
        "b.mp3": {
            "title": "B",
            "artist": "Artist",
            "albumartist": "",
            "album": "Album",
            "track_number": 1,
            "disc_number": 1,
        },
        "single.mp3": {
            "title": "Single",
            "artist": "Other",
            "albumartist": "",
            "album": "Other Album",
            "track_number": 1,
            "disc_number": 1,
        },
    }
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path),
        read_staging_file_metadata=_metadata_for(metadata),
        logger=_FakeLogger(),
    )

    payload, status = staging_groups(runtime)

    assert status == 200
    assert payload["success"] is True
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["album"] == "Album"
    assert group["artist"] == "Artist"
    assert group["file_count"] == 2
    assert [f["filename"] for f in group["files"]] == ["b.mp3", "a.mp3"]


def test_staging_hints_prefers_tag_queries_then_folder_queries(tmp_path):
    _touch(tmp_path / "Folder_Album" / "01.mp3")
    _touch(tmp_path / "Folder_Album" / "02.mp3")
    _touch(tmp_path / "Loose" / "track.flac")

    def _read_tags(file_path):
        if file_path.endswith("01.mp3") or file_path.endswith("02.mp3"):
            return {"album": ["Tagged Album"], "artist": ["Tagged Artist"]}
        return {}

    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path),
        read_tags=_read_tags,
        logger=_FakeLogger(),
    )

    payload, status = staging_hints(runtime)

    assert status == 200
    assert payload == {
        "success": True,
        "hints": ["Tagged Album Tagged Artist", "Folder Album", "Loose"],
    }


def test_staging_suggestions_returns_cache_payload(monkeypatch):
    monkeypatch.setattr(
        import_routes,
        "get_import_suggestions_cache",
        lambda: {"suggestions": [{"album": "Album"}], "built": True},
    )

    payload, status = staging_suggestions()

    assert status == 200
    assert payload == {
        "success": True,
        "suggestions": [{"album": "Album"}],
        "ready": True,
    }


def test_staging_groups_returns_empty_for_missing_staging_path(tmp_path):
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path / "missing"),
        logger=_FakeLogger(),
    )

    payload, status = staging_groups(runtime)

    assert status == 200
    assert payload == {"success": True, "groups": []}


def test_staging_hints_returns_empty_for_missing_staging_path(tmp_path):
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path / "missing"),
        logger=_FakeLogger(),
    )

    payload, status = staging_hints(runtime)

    assert status == 200
    assert payload == {"success": True, "hints": []}


def test_staging_files_returns_error_when_path_resolution_fails():
    logger = _FakeLogger()
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: (_ for _ in ()).throw(RuntimeError("path boom")),
        logger=logger,
    )

    payload, status = staging_files(runtime)

    assert status == 500
    assert payload["success"] is False
    assert payload["error"] == "path boom"
    assert logger.error_messages == ["Error scanning staging files: path boom"]


def test_staging_groups_returns_error_when_metadata_read_fails(tmp_path):
    _touch(tmp_path / "a.mp3")
    logger = _FakeLogger()
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: str(tmp_path),
        read_staging_file_metadata=lambda _file_path, _rel_path: (_ for _ in ()).throw(RuntimeError("tag boom")),
        logger=logger,
    )

    payload, status = staging_groups(runtime)

    assert status == 500
    assert payload["success"] is False
    assert payload["error"] == "tag boom"
    assert logger.error_messages == ["Error building staging groups: tag boom"]


def test_staging_hints_returns_error_when_path_resolution_fails():
    logger = _FakeLogger()
    runtime = ImportRouteRuntime(
        get_staging_path=lambda: (_ for _ in ()).throw(RuntimeError("hint boom")),
        logger=logger,
    )

    payload, status = staging_hints(runtime)

    assert status == 500
    assert payload["success"] is False
    assert payload["error"] == "hint boom"
    assert logger.error_messages == ["Error getting staging hints: hint boom"]


def test_search_albums_enqueues_hydrabase_and_caps_limit():
    worker = _FakeHydrabaseWorker()
    calls = []
    runtime = ImportRouteRuntime(
        get_primary_source=lambda: "hydrabase",
        hydrabase_worker=worker,
        dev_mode_enabled=True,
        search_import_albums=lambda query, limit: calls.append((query, limit)) or [{"id": "album-1"}],
        logger=_FakeLogger(),
    )

    payload, status = search_albums(runtime, "  Album  ", 99)

    assert status == 200
    assert payload == {"success": True, "albums": [{"id": "album-1"}]}
    assert worker.enqueued == [("Album", "albums")]
    assert calls == [("Album", 50)]


def test_search_albums_requires_query():
    payload, status = search_albums(ImportRouteRuntime(logger=_FakeLogger()), "", 12)

    assert status == 400
    assert payload == {"success": False, "error": "Missing query parameter"}


def test_search_tracks_enqueues_hydrabase_and_caps_limit():
    worker = _FakeHydrabaseWorker()
    calls = []
    runtime = ImportRouteRuntime(
        get_primary_source=lambda: "hydrabase",
        hydrabase_worker=worker,
        dev_mode_enabled=True,
        search_import_tracks=lambda query, limit: calls.append((query, limit)) or [{"id": "track-1"}],
        logger=_FakeLogger(),
    )

    payload, status = search_tracks(runtime, "  Track  ", 99)

    assert status == 200
    assert payload == {"success": True, "tracks": [{"id": "track-1"}]}
    assert worker.enqueued == [("Track", "tracks")]
    assert calls == [("Track", 30)]


def test_album_match_warns_without_source_and_passes_file_filter():
    logger = _FakeLogger()
    calls = []
    runtime = ImportRouteRuntime(
        build_album_import_match_payload=lambda *args, **kwargs: calls.append((args, kwargs)) or {"success": True, "matches": []},
        logger=logger,
    )

    payload, status = album_match(
        runtime,
        {
            "album_id": "album-1",
            "album_name": "Album",
            "album_artist": "Artist",
            "file_paths": ["a.flac", "b.flac"],
        },
    )

    assert status == 200
    assert payload == {"success": True, "matches": []}
    assert calls == [
        (
            ("album-1",),
            {
                "album_name": "Album",
                "album_artist": "Artist",
                "file_paths": {"a.flac", "b.flac"},
                "source": None,
            },
        )
    ]
    assert len(logger.warning_messages) == 1
    assert "Missing 'source'" in logger.warning_messages[0]


def test_album_match_requires_album_id():
    payload, status = album_match(ImportRouteRuntime(logger=_FakeLogger()), {})

    assert status == 400
    assert payload == {"success": False, "error": "Missing album_id"}


def test_album_process_posts_valid_files_and_records_side_effects(tmp_path):
    good_file = tmp_path / "good.flac"
    _touch(good_file)
    processed_contexts = []
    activity = []
    refresh_calls = []
    automation = _FakeAutomationEngine()
    runtime = ImportRouteRuntime(
        resolve_album_artist_context=lambda album, source="": {"name": "Artist"},
        build_album_import_context=lambda album, track, **kwargs: {"album": album, "track": track, **kwargs},
        post_process_matched_download=lambda key, context, path: processed_contexts.append((key, context, path)),
        add_activity_item=lambda *args: activity.append(args),
        refresh_import_suggestions_cache=lambda: refresh_calls.append("refresh"),
        automation_engine=automation,
        logger=_FakeLogger(),
    )

    payload, status = album_process(
        runtime,
        {
            "album": {"id": "album-1", "name": "Album", "artist": "Artist", "source": "deezer"},
            "matches": [
                {
                    "staging_file": {"full_path": str(good_file), "filename": "good.flac"},
                    "track": {"name": "Good Track", "track_number": 1, "disc_number": 2},
                },
                {
                    "staging_file": {"full_path": str(tmp_path / "missing.flac"), "filename": "missing.flac"},
                    "track": {"name": "Missing Track", "track_number": 2},
                },
            ],
        },
    )

    assert status == 200
    assert payload == {
        "success": True,
        "processed": 1,
        "total": 2,
        "errors": ["File not found: missing.flac"],
    }
    assert len(processed_contexts) == 1
    key, context, path = processed_contexts[0]
    assert key.startswith("import_album_album-1_1_")
    assert context["artist_context"] == {"name": "Artist"}
    assert context["total_discs"] == 2
    assert context["source"] == "deezer"
    assert path == str(good_file)
    assert activity == [("", "Album Imported", "Album by Artist (1/2 tracks)", "Now")]
    assert refresh_calls == ["refresh"]
    assert automation.events == [
        (
            "import_completed",
            {"track_count": "1", "album_name": "Album", "artist": "Artist"},
        ),
        (
            "batch_complete",
            {
                "playlist_name": "Import: Album",
                "total_tracks": "2",
                "completed_tracks": "1",
                "failed_tracks": "1",
            },
        ),
    ]


def test_album_process_requires_album_and_matches():
    payload, status = album_process(ImportRouteRuntime(logger=_FakeLogger()), {"album": {}, "matches": []})

    assert status == 400
    assert payload == {"success": False, "error": "Missing album or matches data"}


def test_process_single_import_file_resolves_and_posts_context(tmp_path):
    audio_file = tmp_path / "Artist - Song.flac"
    _touch(audio_file)
    post_calls = []
    runtime = ImportRouteRuntime(
        parse_filename_metadata=lambda filename: {"title": "Song", "artist": "Artist"},
        get_single_track_import_context=lambda title, artist, **kwargs: {
            "source": "deezer",
            "context": {"track": {"name": title}, "artist": {"name": artist}},
        },
        normalize_import_context=lambda context: context,
        get_import_context_artist=lambda context: context["artist"],
        get_import_track_info=lambda context: context["track"],
        post_process_matched_download=lambda key, context, path: post_calls.append((key, context, path)),
        logger=_FakeLogger(),
    )

    outcome = process_single_import_file(runtime, {"full_path": str(audio_file), "filename": audio_file.name})

    assert outcome == ("ok", "Song")
    assert len(post_calls) == 1
    assert post_calls[0][0].startswith("import_single_")
    assert post_calls[0][1] == {"track": {"name": "Song"}, "artist": {"name": "Artist"}}
    assert post_calls[0][2] == str(audio_file)


def test_process_single_import_file_rejects_malformed_manual_match(tmp_path):
    audio_file = tmp_path / "Song.flac"
    _touch(audio_file)
    runtime = ImportRouteRuntime(post_process_matched_download=lambda *_args: None, logger=_FakeLogger())

    outcome = process_single_import_file(
        runtime,
        {"full_path": str(audio_file), "filename": audio_file.name, "manual_match": {"id": "track-1"}},
    )

    assert outcome == ("error", "Malformed manual match for file: Song.flac")


def test_singles_process_aggregates_worker_results_and_side_effects():
    activity = []
    refresh_calls = []
    automation = _FakeAutomationEngine()
    executor = _FakeExecutor(outcomes=[("ok", "Song"), ("error", "Bad Song")])
    runtime = ImportRouteRuntime(
        import_singles_executor=executor,
        add_activity_item=lambda *args: activity.append(args),
        refresh_import_suggestions_cache=lambda: refresh_calls.append("refresh"),
        automation_engine=automation,
        logger=_FakeLogger(),
    )

    payload, status = singles_process(
        runtime,
        [{"filename": "a.flac"}, {"filename": "b.flac"}],
    )

    assert status == 200
    assert payload == {
        "success": True,
        "processed": 1,
        "total": 2,
        "errors": ["Bad Song"],
    }
    assert len(executor.calls) == 2
    assert activity == [("", "Singles Imported", "1/2 tracks processed", "Now")]
    assert refresh_calls == ["refresh"]
    assert automation.events == [
        (
            "import_completed",
            {"track_count": "1", "album_name": "", "artist": "Various"},
        ),
        (
            "batch_complete",
            {
                "playlist_name": "Import: Singles",
                "total_tracks": "2",
                "completed_tracks": "1",
                "failed_tracks": "1",
            },
        ),
    ]


def test_singles_process_uses_injected_single_file_worker():
    calls = []
    executor = _FakeExecutor()

    def fake_process_file(runtime, file_info):
        calls.append((runtime, file_info))
        return ("ok", file_info["filename"])

    runtime = ImportRouteRuntime(
        import_singles_executor=executor,
        process_single_import_file=fake_process_file,
        logger=_FakeLogger(),
    )

    payload, status = singles_process(runtime, [{"filename": "patched.flac"}])

    assert status == 200
    assert payload == {
        "success": True,
        "processed": 1,
        "total": 1,
        "errors": [],
    }
    assert calls == [(runtime, {"filename": "patched.flac"})]
    assert executor.calls[0][0] is fake_process_file


def test_singles_process_requires_files():
    payload, status = singles_process(ImportRouteRuntime(logger=_FakeLogger()), [])

    assert status == 400
    assert payload == {"success": False, "error": "No files provided"}
