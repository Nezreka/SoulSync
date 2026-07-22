"""Bundle inventory collection and import lifecycle persistence tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.blocklist import active_blocklisted_dedupe_keys
from core.acquisition.bundle_inventory import (
    INVENTORY_NO_AUDIO_FILES,
    INVENTORY_OK,
    INVENTORY_PATH_UNREADABLE,
    collect_bundle_inventory,
    parse_position,
)
from core.acquisition.candidates import get_candidate, register_candidate
from core.acquisition.grabs import record_grab
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import,
    record_download_completed,
    record_import_deferred,
    record_import_failure,
    record_inventory_result,
)
from core.acquisition.requests import create_request, get_request, transition_request


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _pending_import(conn, download_id: str = "dl-1", output_path: str = "/remote/album"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=10,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=f"import-{download_id}",
    )
    transition_request(conn, request.id, "searching")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=f"ref-{download_id}",
        title="Artist - Album",
        indexer="test-indexer",
        guid=f"guid-{download_id}",
    )
    transition_request(conn, request.id, "candidates_ready")
    record_grab(
        conn,
        download_id,
        "usenet",
        title="Artist - Album",
        category="soulsync",
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
    )
    transition_request(conn, request.id, "grabbing")
    pending = record_download_completed(
        conn, download_id, output_path=output_path)
    return pending, request, candidate


def _fake_tag_reader(mapping):
    def reader(file_path: str):
        return mapping.get(Path(file_path).name, {"available": False})
    return reader


# ---------------------------------------------------------------------------
# parse_position
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7, 7),
        ("7", 7),
        ("07", 7),
        ("7/12", 7),
        (" 3 / 10 ", 3),
        ("", None),
        (None, None),
        ("A1", None),
        ("0", None),
        (-2, None),
        (True, None),
    ],
)
def test_parse_position(value, expected):
    assert parse_position(value) == expected


# ---------------------------------------------------------------------------
# collect_bundle_inventory
# ---------------------------------------------------------------------------


def test_inventory_walks_audio_reads_tags_and_relative_paths(tmp_path):
    (tmp_path / "CD1").mkdir()
    (tmp_path / "CD1" / "01 - Intro.flac").write_bytes(b"x" * 11)
    (tmp_path / "02 - Song.mp3").write_bytes(b"y" * 7)
    (tmp_path / "cover.jpg").write_bytes(b"z")
    reader = _fake_tag_reader({
        "01 - Intro.flac": {
            "available": True,
            "format": "FLAC",
            "bitrate": 900_000,
            "duration": 61.5,
            "tags": {
                "title": "Intro",
                "artist": "Artist",
                "album": "Album",
                "tracknumber": "1/12",
                "discnumber": "1/2",
            },
        },
        "02 - Song.mp3": {
            "available": True,
            "format": "MP3",
            "bitrate": 320_000,
            "duration": 200.0,
            "tags": {"title": "Song", "tracknumber": "2"},
        },
    })
    result = collect_bundle_inventory(
        str(tmp_path),
        path_resolver=lambda path, _config: path,
        tag_reader=reader,
    )
    assert result.ok
    assert result.status == INVENTORY_OK
    assert result.resolved_path == str(tmp_path)
    assert [item.relative_path for item in result.files] == [
        "02 - Song.mp3", "CD1/01 - Intro.flac"]
    intro = result.files[1]
    assert intro.size_bytes == 11
    assert intro.container == "FLAC"
    assert intro.duration_seconds == 61.5
    assert intro.track_number == 1
    assert intro.disc_number == 1
    assert intro.title == "Intro"
    song = result.files[0]
    assert song.track_number == 2
    assert song.disc_number is None
    assert song.tags_available


def test_inventory_applies_remote_path_mapping(tmp_path):
    (tmp_path / "01.flac").write_bytes(b"x")
    seen = {}

    def resolver(reported, config_get):
        seen["reported"] = reported
        return str(tmp_path)

    result = collect_bundle_inventory(
        "/sab-container/downloads/album",
        path_resolver=resolver,
        tag_reader=_fake_tag_reader({}),
    )
    assert result.ok
    assert seen["reported"] == "/sab-container/downloads/album"
    assert result.reported_path == "/sab-container/downloads/album"
    assert result.resolved_path == str(tmp_path)


def test_inventory_unreadable_path_is_retryable(tmp_path):
    missing = tmp_path / "gone"
    result = collect_bundle_inventory(
        str(missing), path_resolver=lambda path, _config: path)
    assert result.status == INVENTORY_PATH_UNREADABLE
    assert result.retryable
    assert not result.files
    assert "remote path mappings" in (result.error or "")


def test_inventory_empty_output_path_is_retryable():
    result = collect_bundle_inventory("   ")
    assert result.status == INVENTORY_PATH_UNREADABLE
    assert result.retryable


def test_inventory_without_audio_is_terminal(tmp_path):
    (tmp_path / "readme.txt").write_text("no audio here")
    result = collect_bundle_inventory(
        str(tmp_path), path_resolver=lambda path, _config: path)
    assert result.status == INVENTORY_NO_AUDIO_FILES
    assert not result.retryable
    assert not result.files


def test_inventory_single_audio_file_path(tmp_path):
    audio = tmp_path / "Artist - Song.flac"
    audio.write_bytes(b"x" * 5)
    result = collect_bundle_inventory(
        str(audio),
        path_resolver=lambda path, _config: path,
        tag_reader=_fake_tag_reader({}),
    )
    assert result.ok
    assert [item.relative_path for item in result.files] == ["Artist - Song.flac"]


def test_inventory_tolerates_raising_tag_reader(tmp_path):
    (tmp_path / "01.flac").write_bytes(b"x")

    def broken_reader(_file_path):
        raise RuntimeError("mutagen exploded")

    result = collect_bundle_inventory(
        str(tmp_path),
        path_resolver=lambda path, _config: path,
        tag_reader=broken_reader,
    )
    assert result.ok
    assert result.files[0].tags_available is False
    assert result.files[0].title is None


# ---------------------------------------------------------------------------
# Persistence: record_inventory_result
# ---------------------------------------------------------------------------


def test_record_inventory_result_enters_matching_once(conn):
    pending, request, _candidate = _pending_import(conn)
    files = [{"relative_path": "01.flac", "size_bytes": 10, "track_number": 1}]
    updated = record_inventory_result(
        conn, pending.id, files, resolved_path="/local/album")
    assert updated.status == "matching"
    assert updated.resolved_path == "/local/album"
    assert updated.error is None
    assert updated.inventory == (files[0],)

    events = [
        event.event_type
        for event in list_history_events(conn, request_id=request.id)
    ]
    assert events.count("import_started") == 1

    # Refresh while already matching: no second history event.
    refreshed = record_inventory_result(
        conn, pending.id, files, resolved_path="/local/album2")
    assert refreshed.status == "matching"
    assert refreshed.resolved_path == "/local/album2"
    events = [
        event.event_type
        for event in list_history_events(conn, request_id=request.id)
    ]
    assert events.count("import_started") == 1


def test_record_inventory_result_requires_files_and_path(conn):
    pending, _request, _candidate = _pending_import(conn)
    with pytest.raises(ValueError, match="at least one audio file"):
        record_inventory_result(conn, pending.id, [], resolved_path="/local")
    with pytest.raises(ValueError, match="resolved local path"):
        record_inventory_result(
            conn, pending.id, [{"relative_path": "01.flac"}], resolved_path=" ")
    with pytest.raises(ValueError, match="must be objects"):
        record_inventory_result(
            conn, pending.id, ["not-a-dict"], resolved_path="/local")
    assert get_import(conn, pending.id).status == "pending"


def test_record_inventory_result_rejects_terminal_import(conn):
    pending, _request, _candidate = _pending_import(conn)
    record_import_failure(
        conn, pending.id, error="broken", failure_kind="runtime")
    with pytest.raises(ValueError, match="terminal"):
        record_inventory_result(
            conn, pending.id, [{"relative_path": "01.flac"}],
            resolved_path="/local")


def test_unknown_import_raises_key_error(conn):
    with pytest.raises(KeyError):
        record_inventory_result(
            conn, "aim1-missing", [{"relative_path": "01.flac"}],
            resolved_path="/local")


# ---------------------------------------------------------------------------
# Persistence: record_import_deferred
# ---------------------------------------------------------------------------


def test_record_import_deferred_counts_attempts_without_history(conn):
    pending, request, _candidate = _pending_import(conn)
    first = record_import_deferred(
        conn, pending.id, error="mount offline http://user:pass@host/x")
    second = record_import_deferred(conn, pending.id, error="mount offline")
    assert first.status == "pending"
    assert second.attempts == 2
    assert "pass" not in (first.error or "")
    events = [
        event.event_type
        for event in list_history_events(conn, request_id=request.id)
    ]
    assert "import_failed" not in events
    assert get_request(conn, request.id).status == "grabbing"


# ---------------------------------------------------------------------------
# Persistence: record_import_failure
# ---------------------------------------------------------------------------


def test_candidate_import_failure_fails_request_and_blocklists(conn):
    pending, request, candidate = _pending_import(conn)
    failed = record_import_failure(
        conn,
        pending.id,
        error="Completed download contains no audio files",
        failure_kind="candidate",
    )
    assert failed.status == "failed"
    assert failed.completed_at is not None
    assert get_request(conn, request.id).status == "failed"
    dedupe_key = get_candidate(conn, candidate.id).dedupe_key
    assert dedupe_key in active_blocklisted_dedupe_keys(conn)
    events = [
        event.event_type
        for event in list_history_events(conn, request_id=request.id)
    ]
    assert "import_failed" in events
    assert "candidate_blocklisted" in events


def test_runtime_import_failure_keeps_candidate_grabbable(conn):
    pending, request, candidate = _pending_import(conn, download_id="dl-2")
    record_import_failure(
        conn, pending.id, error="disk full", failure_kind="runtime")
    assert get_request(conn, request.id).status == "failed"
    dedupe_key = get_candidate(conn, candidate.id).dedupe_key
    assert dedupe_key not in active_blocklisted_dedupe_keys(conn)


def test_import_failure_requires_known_kind(conn):
    pending, _request, _candidate = _pending_import(conn, download_id="dl-3")
    with pytest.raises(ValueError, match="candidate|runtime"):
        record_import_failure(
            conn, pending.id, error="x", failure_kind="client")
    with pytest.raises(ValueError, match="terminal"):
        record_import_failure(
            conn,
            record_import_failure(
                conn, pending.id, error="x", failure_kind="runtime").id,
            error="again",
            failure_kind="runtime",
        )


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_schema_upgrade_adds_new_columns_to_existing_table():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    # Phase-4 layout without resolved_path/attempts.
    connection.execute("""
        CREATE TABLE acquisition_imports (
            id TEXT PRIMARY KEY,
            download_id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL,
            candidate_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            output_path TEXT NOT NULL,
            expected_scope TEXT NOT NULL,
            expected_entity_id INTEGER NOT NULL,
            inventory_json TEXT NOT NULL DEFAULT '[]',
            matches_json TEXT NOT NULL DEFAULT '[]',
            rejections_json TEXT NOT NULL DEFAULT '[]',
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    connection.execute(
        """INSERT INTO acquisition_imports(
               id, download_id, request_id, output_path,
               expected_scope, expected_entity_id)
           VALUES('aim1-old','dl-old','req-old','/remote','release_edition',5)""",
    )
    ensure_acquisition_schema(connection)
    row = get_import(connection, "aim1-old")
    assert row is not None
    assert row.resolved_path is None
    assert row.attempts == 0
    public = row.to_public_dict()
    assert public["has_resolved_path"] is False
    assert public["attempts"] == 0
    connection.close()


def test_inventory_json_round_trips_through_row(conn):
    pending, _request, _candidate = _pending_import(conn, download_id="dl-4")
    files = [
        {
            "relative_path": "CD2/07 - Song.flac",
            "size_bytes": 123,
            "container": "FLAC",
            "bitrate": None,
            "duration_seconds": 199.2,
            "title": "Söng",
            "artist": "Ärtist",
            "album": "Album",
            "track_number": 7,
            "disc_number": 2,
            "tags_available": True,
        },
    ]
    record_inventory_result(conn, pending.id, files, resolved_path="/local")
    raw = conn.execute(
        "SELECT inventory_json FROM acquisition_imports WHERE id=?",
        (pending.id,),
    ).fetchone()[0]
    assert json.loads(raw) == files
    assert get_import(conn, pending.id).inventory == (files[0],)
