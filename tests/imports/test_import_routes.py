import os

import core.imports.routes as import_routes
from core.imports.routes import ImportRouteRuntime, staging_files, staging_groups, staging_hints, staging_suggestions


class _FakeLogger:
    def __init__(self):
        self.debug_messages = []
        self.error_messages = []

    def debug(self, msg, *args):
        self.debug_messages.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.error_messages.append(msg % args if args else msg)


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
