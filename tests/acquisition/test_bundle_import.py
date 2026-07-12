"""Atomic bundle import executor tests (audit §13.4 steps 9-11, §17.5).

The executor must be restart-safe at every phase boundary: planning is a
pure function of persisted state, staging is idempotent by size check,
and the completing transaction is the single point where files, import
and request become durable together.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.bundle_import import (
    OUTCOME_COMPLETED,
    OUTCOME_DEFERRED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    execute_ready_import,
    plan_import_files,
    stage_planned_files,
)
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import,
    record_import_completed,
    record_inventory_result,
    record_matching_result,
)
from core.acquisition.requests import get_request

from tests.acquisition.test_bundle_inventory import _pending_import  # noqa: F401


def _catalog_ddl(conn) -> None:
    from core.library2.editions import (
        LIB2_RECORDINGS_DDL,
        LIB2_RELEASE_EDITIONS_DDL,
        LIB2_RELEASE_TRACKS_DDL,
    )
    from core.library2.schema import LIB2_TRACK_FILES_DDL
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lib2_artists ("
        "id INTEGER PRIMARY KEY, name TEXT, quality_profile_id INTEGER)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lib2_albums ("
        "id INTEGER PRIMARY KEY, primary_artist_id INTEGER, title TEXT, "
        "origin TEXT DEFAULT 'library', expected_track_count INTEGER, "
        "track_count INTEGER, quality_profile_id INTEGER, "
        "updated_at TIMESTAMP)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lib2_tracks ("
        "id INTEGER PRIMARY KEY, album_id INTEGER, title TEXT)")
    conn.execute(LIB2_RELEASE_EDITIONS_DDL)
    conn.execute(LIB2_RECORDINGS_DDL)
    conn.execute(LIB2_RELEASE_TRACKS_DDL)
    conn.execute(LIB2_TRACK_FILES_DDL)


def _seed_catalog(conn, *, edition_id_target=10) -> dict:
    """Artist/album/edition/tracks matching _pending_import's entity_id=10."""
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Artist')")
    conn.execute(
        "INSERT INTO lib2_albums(id, primary_artist_id, title, origin) "
        "VALUES(5, 1, 'Album', 'discography')")
    track_ids = []
    for number, title in ((1, "Intro"), (2, "Outro")):
        cursor = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title) VALUES(5, ?)", (title,))
        track_ids.append(cursor.lastrowid)
    conn.execute(
        "INSERT INTO lib2_release_editions("
        "id, release_group_id, is_default, disambiguation) "
        "VALUES(?, 5, 1, 'Deluxe')", (edition_id_target,))
    for number, (title, track_id) in enumerate(
            zip(("Intro", "Outro"), track_ids), start=1):
        recording = conn.execute(
            "INSERT INTO lib2_recordings(title, duration) VALUES(?, ?)",
            (title, 100_000 * number)).lastrowid
        conn.execute(
            """INSERT INTO lib2_release_tracks(
                   release_edition_id, recording_id, track_id,
                   disc_number, track_number)
               VALUES(?,?,?,1,?)""",
            (edition_id_target, recording, track_id, number))
    return {"album_id": 5, "track_ids": track_ids}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "acquisition.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_acquisition_schema(conn)
    _catalog_ddl(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def factory(db_path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


def _importing_row(conn, bundle_dir: Path, *, download_id="dl-i1", seed=True):
    """A fully matched, importing-state row over a real bundle on disk."""
    if seed:
        _seed_catalog(conn)
    catalog = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT track_number, track_id FROM lib2_release_tracks")
    }
    pending, request, candidate = _pending_import(
        conn, download_id=download_id, output_path=str(bundle_dir))
    bundle_dir.mkdir(parents=True, exist_ok=True)
    inventory = []
    matches = []
    for number, title in ((1, "Intro"), (2, "Outro")):
        name = f"{number:02d} - {title}.flac"
        (bundle_dir / name).write_bytes(b"x" * (10 * number))
        inventory.append({
            "relative_path": name,
            "size_bytes": 10 * number,
            "title": title,
            "track_number": number,
        })
        matches.append({
            "expected_key": f"release_track:{number}",
            "release_track_id": number,
            "track_id": catalog[number],
            "disc_number": 1,
            "track_number": number,
            "expected_title": title,
            "relative_path": name,
            "confidence": 1.0,
            "strategy": "position_and_title",
            "warnings": [],
        })
    record_inventory_result(
        conn, pending.id, inventory, resolved_path=str(bundle_dir))
    record_matching_result(
        conn, pending.id, matches, [], decision="import_ready")
    return pending, request, candidate


def _fake_prober(path):
    return SimpleNamespace(
        format="flac", bitrate=900, sample_rate=44100, bit_depth=16)


# ---------------------------------------------------------------------------
# plan_import_files
# ---------------------------------------------------------------------------


def _record_stub(matches, inventory=(), resolved="/bundle"):
    return SimpleNamespace(
        matches=tuple(matches),
        inventory=tuple(inventory),
        resolved_path=resolved,
    )


def test_plan_builds_deterministic_layout():
    record = _record_stub(
        matches=[
            {"relative_path": "01.flac", "track_id": 4, "disc_number": 1,
             "track_number": 1, "expected_title": "Intro"},
        ],
        inventory=[{"relative_path": "01.flac", "size_bytes": 11}],
    )
    planned = plan_import_files(
        record, artist="AC/DC", release_title="Album: One", edition="Deluxe",
        transfer_dir="/lib")
    assert len(planned) == 1
    item = planned[0]
    assert item.source_path == str(Path("/bundle", "01.flac"))
    assert item.destination_path == str(Path(
        "/lib", "AC_DC", "AC_DC - Album_ One (Deluxe)", "01 - Intro.flac"))
    assert item.track_id == 4
    assert item.size_bytes == 11


def test_plan_multi_disc_gets_disc_folders_and_collisions_unique():
    record = _record_stub(matches=[
        {"relative_path": "CD1/01.flac", "track_id": 1, "disc_number": 1,
         "track_number": 1, "expected_title": "Same"},
        {"relative_path": "CD2/01.flac", "track_id": 2, "disc_number": 2,
         "track_number": 1, "expected_title": "Same"},
    ])
    planned = plan_import_files(
        record, artist="A", release_title="B", edition=None,
        transfer_dir="/lib")
    assert [item.destination_path for item in planned] == [
        str(Path("/lib", "A", "A - B", "Disc 1", "01 - Same.flac")),
        str(Path("/lib", "A", "A - B", "Disc 2", "01 - Same.flac")),
    ]

    flat = plan_import_files(
        _record_stub(matches=[
            {"relative_path": "a/01.flac", "track_id": 1, "disc_number": 1,
             "track_number": 1, "expected_title": "Same"},
            {"relative_path": "b/01.flac", "track_id": 2, "disc_number": 1,
             "track_number": 1, "expected_title": "Same"},
        ]),
        artist="A", release_title="B", edition=None, transfer_dir="/lib")
    assert flat[0].destination_path != flat[1].destination_path
    assert "(2)" in flat[1].destination_path


def test_plan_requires_matches_paths_and_track_links():
    with pytest.raises(ValueError, match="no persisted track matches"):
        plan_import_files(
            _record_stub(matches=[]), artist="A", release_title="B",
            edition=None, transfer_dir="/lib")
    with pytest.raises(ValueError, match="resolved bundle path"):
        plan_import_files(
            _record_stub(
                matches=[{"relative_path": "x.flac", "track_id": 1}],
                resolved=""),
            artist="A", release_title="B", edition=None, transfer_dir="/lib")
    with pytest.raises(ValueError, match="lib2 track link"):
        plan_import_files(
            _record_stub(matches=[{"relative_path": "x.flac"}]),
            artist="A", release_title="B", edition=None, transfer_dir="/lib")


# ---------------------------------------------------------------------------
# stage_planned_files
# ---------------------------------------------------------------------------


def _planned(tmp_path, name="01 - Intro.flac", content=b"x" * 10):
    from core.acquisition.bundle_import import PlannedImportFile
    source = tmp_path / "bundle" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    return PlannedImportFile(
        relative_path=name,
        source_path=str(source),
        destination_path=str(tmp_path / "lib" / "Artist" / name),
        track_id=1,
        size_bytes=len(content),
    )


def test_stage_copies_and_is_idempotent(tmp_path):
    item = _planned(tmp_path)
    assert stage_planned_files([item]) is None
    destination = Path(item.destination_path)
    assert destination.read_bytes() == b"x" * 10
    # Second run: size matches, nothing rewritten.
    calls = []
    assert stage_planned_files(
        [item], copier=lambda s, d: calls.append((s, d)) or True) is None
    assert calls == []


def test_stage_reports_missing_source_and_conflicting_destination(tmp_path):
    item = _planned(tmp_path)
    Path(item.source_path).unlink()
    error = stage_planned_files([item])
    assert "disappeared" in error

    item2 = _planned(tmp_path, name="02 - Outro.flac", content=b"y" * 8)
    destination = Path(item2.destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"different-size-content")
    error = stage_planned_files([item2])
    assert "different content" in error


# ---------------------------------------------------------------------------
# record_import_completed
# ---------------------------------------------------------------------------


def test_record_import_completed_requires_importing_and_journal(factory, tmp_path):
    conn = factory()
    try:
        pending, request, _candidate = _importing_row(
            conn, tmp_path / "bundle")
        with pytest.raises(ValueError, match="imported file entries"):
            record_import_completed(conn, pending.id, result={"imported": []})
        completed = record_import_completed(
            conn, pending.id,
            result={"imported": [{"relative_path": "01.flac"}]})
        assert completed.status == "completed"
        assert get_request(conn, request.id).status == "completed"
        events = [
            e.event_type
            for e in list_history_events(conn, request_id=request.id)]
        assert "import_completed" in events
        with pytest.raises(ValueError, match="terminal"):
            record_import_completed(
                conn, pending.id,
                result={"imported": [{"relative_path": "01.flac"}]})
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# execute_ready_import — end to end
# ---------------------------------------------------------------------------


def _config_get(tmp_path):
    def get(key, default=None):
        if key == "soulseek.transfer_path":
            return str(tmp_path / "Transfer")
        return default
    return get


def test_execute_ready_import_completes_files_rows_and_request(
        factory, tmp_path):
    conn = factory()
    try:
        pending, request, _candidate = _importing_row(
            conn, tmp_path / "bundle")
        conn.commit()
    finally:
        conn.close()

    outcome = execute_ready_import(
        factory, pending.id,
        config_get=_config_get(tmp_path), prober=_fake_prober)
    assert outcome.status == OUTCOME_COMPLETED
    assert outcome.imported_files == 2

    expected_dir = (
        tmp_path / "Transfer" / "Artist" / "Artist - Album (Deluxe)")
    assert (expected_dir / "01 - Intro.flac").read_bytes() == b"x" * 10
    assert (expected_dir / "02 - Outro.flac").read_bytes() == b"x" * 20
    # Source bundle untouched (client owns the originals).
    assert (tmp_path / "bundle" / "01 - Intro.flac").exists()

    conn = factory()
    try:
        record = get_import(conn, pending.id)
        assert record.status == "completed"
        assert len(record.result["imported"]) == 2
        assert get_request(conn, request.id).status == "completed"
        rows = conn.execute(
            "SELECT track_id, path, original_path, format, quality_tier, "
            "source, import_status FROM lib2_track_files ORDER BY track_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["format"] == "flac"
        assert rows[0]["quality_tier"] == "lossless"
        assert rows[0]["source"] == "usenet"
        assert rows[0]["import_status"] == "imported"
        assert rows[0]["path"].endswith("01 - Intro.flac")
        assert rows[0]["original_path"].endswith("01 - Intro.flac")
        origin = conn.execute(
            "SELECT origin FROM lib2_albums WHERE id=5").fetchone()[0]
        assert origin == "library"
        events = [
            e.event_type
            for e in list_history_events(conn, request_id=request.id)]
        assert "import_completed" in events
    finally:
        conn.close()


def test_execute_ready_import_is_idempotent_after_completion(
        factory, tmp_path):
    conn = factory()
    try:
        pending, _request, _candidate = _importing_row(
            conn, tmp_path / "bundle")
        conn.commit()
    finally:
        conn.close()
    config = _config_get(tmp_path)
    first = execute_ready_import(
        factory, pending.id, config_get=config, prober=_fake_prober)
    assert first.status == OUTCOME_COMPLETED
    second = execute_ready_import(
        factory, pending.id, config_get=config, prober=_fake_prober)
    assert second.status == OUTCOME_SKIPPED
    assert second.detail == "completed"
    conn = factory()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_execute_ready_import_defers_when_source_vanished(factory, tmp_path):
    bundle = tmp_path / "bundle"
    conn = factory()
    try:
        pending, request, _candidate = _importing_row(conn, bundle)
        conn.commit()
    finally:
        conn.close()
    (bundle / "02 - Outro.flac").unlink()

    outcome = execute_ready_import(
        factory, pending.id,
        config_get=_config_get(tmp_path), prober=_fake_prober)
    assert outcome.status == OUTCOME_DEFERRED
    assert "disappeared" in outcome.detail

    conn = factory()
    try:
        record = get_import(conn, pending.id)
        assert record.status == "importing"
        assert record.attempts == 1
        assert "disappeared" in record.error
        assert get_request(conn, request.id).status == "grabbing"
    finally:
        conn.close()

    # The file returns (mount restored) — the next cycle completes.
    (bundle / "02 - Outro.flac").write_bytes(b"x" * 20)
    retry = execute_ready_import(
        factory, pending.id,
        config_get=_config_get(tmp_path), prober=_fake_prober)
    assert retry.status == OUTCOME_COMPLETED


def test_execute_ready_import_fails_on_broken_plan(factory, tmp_path):
    conn = factory()
    try:
        pending, request, _candidate = _importing_row(
            conn, tmp_path / "bundle")
        # Simulate legacy/corrupt persisted matches without track links.
        conn.execute(
            "UPDATE acquisition_imports SET matches_json=? WHERE id=?",
            (json.dumps([{"relative_path": "01 - Intro.flac"}]), pending.id))
        conn.commit()
    finally:
        conn.close()

    outcome = execute_ready_import(
        factory, pending.id,
        config_get=_config_get(tmp_path), prober=_fake_prober)
    assert outcome.status == OUTCOME_FAILED
    assert "track link" in outcome.detail

    conn = factory()
    try:
        assert get_import(conn, pending.id).status == "failed"
        assert get_request(conn, request.id).status == "failed"
    finally:
        conn.close()


def test_execute_ready_import_skips_non_importing_rows(factory, tmp_path):
    conn = factory()
    try:
        _seed_catalog(conn)
        pending, _request, _candidate = _pending_import(
            conn, download_id="dl-pending", output_path=str(tmp_path))
        conn.commit()
    finally:
        conn.close()
    outcome = execute_ready_import(
        factory, pending.id, config_get=_config_get(tmp_path))
    assert outcome.status == OUTCOME_SKIPPED
    assert outcome.detail == "pending"
    missing = execute_ready_import(
        factory, "aim1-missing", config_get=_config_get(tmp_path))
    assert missing.status == OUTCOME_SKIPPED
